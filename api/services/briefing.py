"""api/services/briefing.py — Briefing de apertura (QuantAI P1, v1 DETERMINISTA).

Arma el contenido del modal de las 10:00 ART de HOME: futuros de índices US,
dólar oficial (mayorista live MAE + A3500 BCRA) y cierres MEP/CCL con variación
vs la rueda anterior. TODO calculado de datos ya ingeridos — sin LLM: son solo
números, y la regla de oro 1 del roadmap dice que la IA nunca es la fuente de
un número. La capa de redacción IA se agrega ARRIBA de esto cuando el briefing
sume narrativa (noticias/calendario); este service es, a la vez, su fallback
determinista.

Manejo de feriados/fin de semana gratis: "cierre anterior" = los dos últimos
días CON DATOS (solo hay filas en días de rueda), no ayer calendario.

Fuentes (verificadas con scripts/diag 2026-07-10):
  - home.market_quotes 'S&P FUT'/'NASDAQ FUT' (Yahoo, cron 1min; pct_day listo)
  - valuaciones.dolar_oficial_live UST$T M/000 (MAE via PC oficina; variacion lista)
  - macro.series_macro serie DOLAR (A3500 BCRA, fixing diario)
  - valuaciones.dolar (histórico MEP/CCL → último valor por día = cierre)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from core.postgres import get_pool

_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_FUTUROS_STALE_MIN = 30  # el poller corre cada 1 min; >30 min = feed caído

# Orden y agrupación de lectura de la mesa. El índice en esta lista fija el orden
# de aparición; el segundo campo agrupa en el cartel. Los labels son los `symbol`
# que escribe jobs/market_quotes (HOME_FUTUROS). Un futuro que no esté acá cae al
# final como "Otros" (no se pierde).
_FUTUROS_ORDEN: list[tuple[str, str]] = [
    ("S&P FUT",    "Índices US"),
    ("NASDAQ FUT", "Índices US"),
    ("WTI",        "Energía"),
    ("BRENT",      "Energía"),
    ("ORO",        "Metales"),
    ("SOJA",       "Granos"),
    ("MAIZ",       "Granos"),
    ("TRIGO",      "Granos"),
    ("BTCUSDT",    "Cripto"),
    ("ETHUSDT",    "Cripto"),
]
_FUT_POS = {lbl: i for i, (lbl, _) in enumerate(_FUTUROS_ORDEN)}
_FUT_GRUPO = dict(_FUTUROS_ORDEN)


def _var_pct(actual: float | None, anterior: float | None) -> float | None:
    if actual is None or not anterior:
        return None
    return round((float(actual) / float(anterior) - 1) * 100, 2)


def _futuros(cur) -> list[dict[str, Any]]:
    """Los 10 futuros del watchlist (índices US, energía, metales, granos, cripto)
    con variación 1d (pct_day), semana (ancla 7d) y mes (ancla MTD). Las anclas las
    escribe jobs/market_anchors en el mismo doc; el retorno se calcula al vuelo."""
    cur.execute(
        "SELECT data FROM home.market_quotes"
        " WHERE data->>'type' = 'future' OR data->>'grupo' = 'Futuros'"
    )
    out = []
    ahora = datetime.now(UTC)
    for fila in cur.fetchall():
        data = fila["data"]
        label = data.get("symbol")
        upd = data.get("updated_at")
        stale = True
        if upd:
            try:
                ts = datetime.fromisoformat(upd)
                stale = (ahora - ts) > timedelta(minutes=_FUTUROS_STALE_MIN)
            except ValueError:
                pass
        last = data.get("last")
        out.append({
            "label":       label,
            "grupo":       _FUT_GRUPO.get(label, "Otros"),
            "last":        last,
            "pct_day":     round(data["pct_day"], 2) if data.get("pct_day") is not None else None,
            "ret_semana":  _var_pct(last, data.get("anchor_7d")),
            "ret_mes":     _var_pct(last, data.get("anchor_mtd")),
            "updated_at":  upd,
            "stale":       stale,
        })
    # Orden de mesa (los no listados van al final, alfabético)
    out.sort(key=lambda r: (_FUT_POS.get(r["label"], 99), r["label"] or ""))
    return out


def _oficial_live(cur, hoy_art) -> dict[str, Any] | None:
    """Mayorista MAE de HOY. Si el feed no operó hoy (PC apagada / sin ruedas),
    devuelve None — el front muestra 'aún sin operaciones' (nunca un número
    viejo disfrazado de vivo)."""
    cur.execute(
        """
        SELECT data, updated_at FROM valuaciones.dolar_oficial_live
        WHERE ticker = 'UST$T' AND codigo_segmento = 'M' AND codigo_plazo = '000'
        """
    )
    fila = cur.fetchone()
    if not fila:
        return None
    data, upd = fila["data"], fila["updated_at"]
    if upd is None or upd.astimezone(_ART).date() != hoy_art:
        return None
    return {
        "valor":         data.get("precioUltimo"),
        "variacion_pct": data.get("variacion"),
        "maximo":        data.get("precioMaximo"),
        "minimo":        data.get("precioMinimo"),
        "ts":            upd,
    }


def _a3500(cur) -> dict[str, Any] | None:
    cur.execute(
        "SELECT fecha, valor FROM macro.series_macro"
        " WHERE serie = 'DOLAR' ORDER BY fecha DESC LIMIT 2"
    )
    filas = cur.fetchall()
    if not filas:
        return None
    ultimo = filas[0]
    anterior = filas[1] if len(filas) > 1 else None
    return {
        "fecha":         ultimo["fecha"],
        "valor":         ultimo["valor"],
        "variacion_pct": _var_pct(ultimo["valor"], anterior["valor"] if anterior else None),
    }


def _mep_ccl(cur) -> dict[str, Any]:
    """Cierre del último día con rueda + variación vs la rueda anterior, para
    mep y ccl. 'Cierre' = último valor del día en valuaciones.dolar."""
    cur.execute(
        """
        SELECT DISTINCT ON (date(timestamp)) date(timestamp) AS dia, mep, ccl
        FROM valuaciones.dolar
        WHERE mep IS NOT NULL AND timestamp >= now() - interval '14 days'
        ORDER BY date(timestamp) DESC, timestamp DESC
        LIMIT 2
        """
    )
    filas = cur.fetchall()
    if not filas:
        return {"mep": None, "ccl": None}
    ult = filas[0]
    ant = filas[1] if len(filas) > 1 else None
    out = {}
    for campo in ("mep", "ccl"):
        out[campo] = {
            "fecha":         ult["dia"],
            "cierre":        ult[campo],
            "variacion_pct": _var_pct(ult[campo], ant[campo] if ant else None),
        }
    return out


def briefing_hoy() -> dict[str, Any]:
    """Payload completo del modal. Cada bloque puede venir None/[] si su fuente
    no tiene datos — el front decide qué renderizar (failing gracefully)."""
    hoy_art = datetime.now(_ART).date()
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        futuros = _futuros(cur)
        oficial_live = _oficial_live(cur, hoy_art)
        a3500 = _a3500(cur)
        cambios = _mep_ccl(cur)
    return {
        "fecha":        hoy_art.isoformat(),
        "generado":     datetime.now(UTC),
        "futuros":      futuros,
        "oficial_live": oficial_live,   # None → "aún sin operaciones hoy"
        "a3500":        a3500,          # último fixing BCRA + var vs anterior
        "mep":          cambios.get("mep"),
        "ccl":          cambios.get("ccl"),
    }
