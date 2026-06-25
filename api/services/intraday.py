"""api/services/intraday.py — monitor intradía de renta variable (FIFO).

Recibe el CSV de boletos del día (export ROFEX/Aunesa, formato AR con `.` de
miles y `,` decimal) y devuelve, por (cuenta, especie):

  - posición neta (compra +, venta −) → estado LONG / SHORT / CERRADA
  - precio ponderado de la posición ABIERTA (lotes vivos, no de todos los trades)
  - PnL realizado (parte cerrada, método FIFO) + no realizado (mark live − ponderado)
  - intereses (monto × 0.0007) + IVA (21 %) por trade → PnL neto

Cauciones (especie PESOS/DOLARES, F.O. "Caución") se EXCLUYEN: esta vista es
100 % para monitorear el día operando acciones/CEDEARs, no el fondeo.

Mark price de la posición abierta: último `last` ARS del feed live
(`mercado.cedears_snapshot`) mapeado por `ticker_corto`. Si no hay snapshot
para esa especie, cae al último precio operado del propio CSV (mark_source).

NO persiste nada — es un cálculo efímero sobre el archivo que sube el usuario.
El FIFO es puro (testeable); la única dependencia externa es el mark live, que
está envuelto en try/except → si el feed no responde, todo cae al precio CSV.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import deque

logger = logging.getLogger("api.intraday")

__all__ = ["IntradayError", "analizar", "fifo_pnl"]

INTERES_RATE = 0.0007   # arancel por trade sobre el monto bruto
IVA_RATE = 0.21         # IVA sobre el arancel

# Especies que NO son renta variable (fondeo): cauciones en pesos/dólares.
_ESPECIES_FONDEO = {"PESOS", "DOLARES", "DOLAR", "USD"}
_COMPRA = {"COMPRA", "COMPRAR", "BUY", "B"}
_VENTA = {"VENTA", "VENDER", "SELL", "S"}


class IntradayError(Exception):
    """CSV inválido / faltan columnas — se traduce a HTTP 400."""


# ── parsing ──────────────────────────────────────────────────────────────────
def _num(s: str | None) -> float:
    """Número en formato AR: '11.630,000' → 11630.0, '4.820,000' → 4820.0.
    Detecta el separador decimal por la posición relativa de ',' y '.'."""
    if not s:
        return 0.0
    s = s.strip().strip('"').strip()
    if not s:
        return 0.0
    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    if last_comma > last_dot:
        s = s.replace(".", "").replace(",", ".")
    elif last_dot > last_comma:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _resolve(headers: list[str], *aliases: str) -> str | None:
    norm = {h.strip().strip('"').strip().lower(): h for h in headers}
    for a in aliases:
        h = norm.get(a.lower())
        if h is not None:
            return h
    return None


def _parse_csv(text: str) -> list[dict]:
    """Texto CSV → lista de trades de renta variable (cauciones excluidas).

    Cada trade: {hora, especie, lado(+1/-1), precio, cantidad, cuenta, monto,
    moneda}. Lanza IntradayError si faltan columnas mínimas."""
    sample = text.split("\n", 1)[0]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise IntradayError("El archivo está vacío.")

    headers = [h.strip().strip('"').strip() for h in rows[0]]
    c_esp = _resolve(headers, "Especie", "Simbolo", "Símbolo", "Symbol")
    c_lado = _resolve(headers, "Lado", "Punta", "Side", "Operación", "Operacion")
    c_precio = _resolve(headers, "Precio", "Price")
    c_cant = _resolve(headers, "Cantidad", "Cantidad Ejecutada", "Executed Size", "Size")
    c_cta = _resolve(headers, "Cuenta", "Account", "Comitente")
    c_monto = _resolve(headers, "Monto", "Turnover", "Importe")
    c_hora = _resolve(headers, "Hora", "Fecha", "Time", "Datetime")
    c_fo = _resolve(headers, "F.O.", "FO", "Tipo")
    c_mon = _resolve(headers, "Moneda", "Currency")

    faltan = [
        name for name, col in (
            ("Especie", c_esp), ("Lado", c_lado), ("Precio", c_precio),
            ("Cantidad", c_cant), ("Monto", c_monto),
        ) if col is None
    ]
    if faltan:
        raise IntradayError(
            f"Faltan columnas: {', '.join(faltan)}. Presentes: {', '.join(headers)}"
        )

    idx = {h: i for i, h in enumerate(headers)}

    def cell(r: list[str], col: str | None) -> str:
        if col is None:
            return ""
        i = idx[col]
        return r[i].strip().strip('"').strip() if i < len(r) else ""

    trades: list[dict] = []
    for r in rows[1:]:
        especie = cell(r, c_esp).upper()
        fo = cell(r, c_fo).upper()
        # Excluir fondeo: caución o especie PESOS/DOLARES.
        if especie in _ESPECIES_FONDEO or fo.startswith("CAUCI"):
            continue
        lado_raw = cell(r, c_lado).upper()
        signo = 1 if lado_raw in _COMPRA else -1 if lado_raw in _VENTA else 0
        if signo == 0:
            continue
        cant = _num(cell(r, c_cant))
        if cant <= 0:
            continue
        trades.append({
            "hora": cell(r, c_hora),
            "especie": especie,
            "signo": signo,
            "precio": _num(cell(r, c_precio)),
            "cantidad": cant,
            "cuenta": cell(r, c_cta) or "—",
            "monto": _num(cell(r, c_monto)),
            "moneda": cell(r, c_mon).upper() or "ARS",
        })
    return trades


# ── FIFO ─────────────────────────────────────────────────────────────────────
def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def fifo_pnl(trades: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Motor FIFO puro. `trades` = [(qty_firmada, precio), ...] en orden
    cronológico (compra +, venta −). Devuelve (pnl_realizado, qty_abierta,
    precio_ponderado_abierto).

    Cierra contra los lotes más viejos del lado opuesto. Si un trade excede los
    lotes opuestos, el remanente abre un lote nuevo (se da vuelta la posición).
    Simétrico para long y short."""
    lots: deque[list[float]] = deque()  # [qty_firmada, precio], todos mismo signo
    realized = 0.0
    for qty, price in trades:
        # Consumir lotes opuestos (cierre FIFO).
        while qty != 0 and lots and _sign(lots[0][0]) != _sign(qty):
            lot = lots[0]
            m = min(abs(lot[0]), abs(qty))            # magnitud que se cierra
            if lot[0] > 0:                            # cierro un long con una venta
                realized += (price - lot[1]) * m
            else:                                     # cierro un short con una compra
                realized += (lot[1] - price) * m
            lot[0] -= _sign(lot[0]) * m
            if lot[0] == 0:
                lots.popleft()
            qty -= _sign(qty) * m
        # Remanente (mismo lado, o vuelta de posición) → lote nuevo.
        if qty != 0:
            lots.append([qty, price])

    open_qty = sum(l[0] for l in lots)
    cost = sum(l[0] * l[1] for l in lots)
    wavg = cost / open_qty if open_qty else 0.0
    return realized, open_qty, wavg


# ── mark live ────────────────────────────────────────────────────────────────
def _marks_live(tickers_corto: set[str]) -> dict[str, dict]:
    """ticker_corto → {last, updated_at} desde mercado.cedears_snapshot.

    Robusto a que el feed no esté: cualquier error devuelve {} y el caller cae
    al último precio del CSV. No filtra por ticker en SQL (el universo es chico)
    para una sola query."""
    if not tickers_corto:
        return {}
    try:
        from psycopg.rows import dict_row

        from core.postgres import get_pool

        out: dict[str, dict] = {}
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.ticker_corto AS tc,
                       (s.data->>'last')::numeric AS last,
                       s.updated_at AS ua
                FROM mercado.cedears c
                JOIN mercado.cedears_snapshot s ON s.ticker = c.data->>'ticker'
                WHERE c.ticker_corto IS NOT NULL
                """
            )
            for row in cur.fetchall():
                tc = (row["tc"] or "").upper()
                last = row["last"]
                if tc and last is not None and float(last) > 0:
                    ua = row["ua"]
                    out[tc] = {
                        "last": float(last),
                        "updated_at": ua.isoformat() if hasattr(ua, "isoformat") else ua,
                    }
        return out
    except Exception as e:  # feed opcional, nunca debe romper el cálculo
        logger.warning("intraday: mark live no disponible (%s) — uso precio CSV", e)
        return {}


# ── orquestación ─────────────────────────────────────────────────────────────
def analizar(csv_text: str, *, archivo: str | None = None) -> dict:
    """Pipeline completo: parsea → FIFO por (cuenta, especie) → intereses/IVA →
    mark live → totales. Devuelve el shape que consume el frontend."""
    trades = _parse_csv(csv_text)

    # Agrupar por (cuenta, especie) preservando orden cronológico de aparición.
    grupos: dict[tuple[str, str], list[dict]] = {}
    for t in trades:
        grupos.setdefault((t["cuenta"], t["especie"]), []).append(t)

    marks = _marks_live({esp for _, esp in grupos})

    posiciones: list[dict] = []
    for (cuenta, especie), ts in grupos.items():
        compras_qty = sum(t["cantidad"] for t in ts if t["signo"] > 0)
        ventas_qty = sum(t["cantidad"] for t in ts if t["signo"] < 0)
        intereses = sum(t["monto"] * INTERES_RATE for t in ts)
        iva = intereses * IVA_RATE

        realized, open_qty, wavg = fifo_pnl(
            [(t["signo"] * t["cantidad"], t["precio"]) for t in ts]
        )

        # Mark: live por ticker_corto; si no hay, último precio operado del CSV.
        mk = marks.get(especie)
        if mk:
            mark, mark_source, mark_ts = mk["last"], "live", mk.get("updated_at")
        else:
            mark, mark_source, mark_ts = ts[-1]["precio"], "csv", None

        unreal = open_qty * (mark - wavg) if abs(open_qty) > 1e-9 else 0.0
        bruto = realized + unreal
        estado = "CERRADA" if abs(open_qty) < 1e-9 else ("LONG" if open_qty > 0 else "SHORT")

        posiciones.append({
            "cuenta": cuenta,
            "especie": especie,
            "moneda": ts[0]["moneda"],
            "n_ops": len(ts),
            "compras_qty": compras_qty,
            "ventas_qty": ventas_qty,
            "qty_neta": open_qty,
            "estado": estado,
            "precio_ponderado": wavg if abs(open_qty) > 1e-9 else None,
            "mark": mark,
            "mark_source": mark_source,
            "mark_updated_at": mark_ts,
            "monto_abierto": open_qty * wavg if abs(open_qty) > 1e-9 else 0.0,
            "valor_actual": open_qty * mark if abs(open_qty) > 1e-9 else 0.0,
            "pnl_realizado": realized,
            "pnl_no_realizado": unreal,
            "pnl_bruto": bruto,
            "intereses": intereses,
            "iva": iva,
            "pnl_neto": bruto - intereses - iva,
            "trades": [
                {
                    "hora": x["hora"],
                    "lado": "Compra" if x["signo"] > 0 else "Venta",
                    "precio": x["precio"],
                    "cantidad": x["cantidad"],
                    "monto": x["monto"],
                }
                for x in ts
            ],
        })

    # Orden: abiertas primero (por |valor|), después cerradas (por |PnL|).
    posiciones.sort(
        key=lambda p: (p["estado"] == "CERRADA", -abs(p["valor_actual"] or p["pnl_bruto"]))
    )

    tot = {
        "pnl_realizado": sum(p["pnl_realizado"] for p in posiciones),
        "pnl_no_realizado": sum(p["pnl_no_realizado"] for p in posiciones),
        "pnl_bruto": sum(p["pnl_bruto"] for p in posiciones),
        "intereses": sum(p["intereses"] for p in posiciones),
        "iva": sum(p["iva"] for p in posiciones),
        "pnl_neto": sum(p["pnl_neto"] for p in posiciones),
        "abiertas": sum(1 for p in posiciones if p["estado"] != "CERRADA"),
        "cerradas": sum(1 for p in posiciones if p["estado"] == "CERRADA"),
    }

    return {
        "archivo": archivo,
        "filas_validas": len(trades),
        "posiciones": posiciones,
        "totales": tot,
    }
