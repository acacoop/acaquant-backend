"""Motor de PnL por (cuenta, ticker) basado en cash flows.

Filosofía: comparar dinero PAGADO contra (valor ACTUAL + dinero COBRADO).
Esto captura comisiones automáticamente porque el `importe` de cada boleto
ya viene neto del lado de Aunesa.

Para cada ticker:
    cash_neto = Σ importe (con signo nativo) en categorías:
                {compra, venta, suscripcion_fci, rescate_fci, acreencia}
    valor_actual = cantidad × precio del último snapshot AuM
    pnl_total    = valor_actual + cash_neto
    pnl_pct      = pnl_total / cash_pagado

`acreencia.op` se desglosa en {Cash dividend, Interest payment,
Partial redemption} — la UI los muestra por separado.

Pesificación: cada importe USD/USDC se convierte al MEP de su fecha
para sumar consistente en ARS. Si una fecha no tiene MEP, el importe
queda en moneda original (fallback) y se reporta en `fechas_sin_mep`.

Limitación conocida: si la cuenta tenía posiciones ANTES del primer
boleto disponible (data más vieja que NegocioMovimientos), `cash_pagado`
está subestimado y el pnl_pct va a estar inflado. Detectamos esto
comparando cantidad neta de boletos vs cantidad actual del AuM y
seteamos `completeness = "parcial"`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from api.cache import cached
from api.db import get_db_cashflow, get_db_valuaciones
from api.services._mep import get_mep_for_date

_CATS_PAGO        = {"compra", "suscripcion_fci"}
_CATS_COBRO_VENTA = {"venta", "rescate_fci"}
_CATS_COBRO_PASIVO = {"acreencia"}
_CATS_RELEVANTES  = _CATS_PAGO | _CATS_COBRO_VENTA | _CATS_COBRO_PASIVO

# Tipos de op dentro de acreencia. Si Aunesa devuelve uno nuevo, va al
# bucket "Otros" para que el total siga cuadrando.
_OPS_PASIVOS = ("Cash dividend", "Interest payment", "Partial redemption")

# Regex para extraer ticker corto de "[NN] TICKER" → "TICKER".
_RE_TICKER_CORTO = re.compile(r"^\[\d+\]\s*(.+)$")


def _ticker_corto(unidad: str) -> str:
    """Convierte unidad de AuM ("[5921] AL30") al ticker que usa
    NegocioMovimientos ("AL30")."""
    m = _RE_TICKER_CORTO.match(unidad or "")
    return m.group(1).strip() if m else (unidad or "")


@cached(ttl=300)
def pnl_por_cuenta(id_cuenta: str) -> dict:
    """PnL por ticker para una cuenta. Ver docstring del módulo."""
    db_cf = get_db_cashflow()
    db_v = get_db_valuaciones()

    # ── 1. Boletos relevantes ────────────────────────────────────────
    boletos = list(db_cf["NegocioMovimientos"].find(
        {
            "cuenta":    {"$regex": f"^\\[{id_cuenta}\\]"},
            "categoria": {"$in": list(_CATS_RELEVANTES)},
            "ticker":    {"$ne": None},
        },
        {"_id": 0, "fecha": 1, "categoria": 1, "op": 1,
         "ticker": 1, "cantidad": 1, "importe": 1, "moneda": 1},
    ))

    # ── 2. Pesificación al MEP de cada fecha ─────────────────────────
    mep_cache: dict[str, float | None] = {}

    def _importe_ars(importe: float, moneda: str, fecha: str) -> tuple[float, bool]:
        if moneda == "ARS":
            return importe, False
        if fecha not in mep_cache:
            mep_cache[fecha] = get_mep_for_date(fecha)
        mep = mep_cache[fecha]
        if mep is None or mep <= 0:
            return importe, True  # fallback: queda en USD/USDC
        return importe * mep, False

    # ── 3. Acumular por ticker ───────────────────────────────────────
    def _new_state() -> dict:
        return {
            "cash_pagado":         0.0,   # |Σ importes negativos|
            "cash_cobrado_venta":  0.0,   # Σ importes ventas / rescates
            "cash_cobrado_pasivo": 0.0,   # Σ importes acreencia
            "breakdown_pasivo":    {op: 0.0 for op in _OPS_PASIVOS},
            "breakdown_otros":     0.0,   # acreencia con op desconocido
            "qty_compras":         0.0,
            "qty_ventas":          0.0,
            "monedas":             set(),
            "fechas_sin_mep":      set(),
            "n_movimientos":       0,
        }

    state: dict[str, dict] = defaultdict(_new_state)

    for b in boletos:
        ticker = (b.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            importe = float(b.get("importe") or 0)
        except (TypeError, ValueError):
            continue
        if importe == 0:
            continue

        moneda = b.get("moneda") or "ARS"
        fecha  = b.get("fecha") or ""
        imp_ars, mep_missing = _importe_ars(importe, moneda, fecha)

        cat = b.get("categoria")
        op  = b.get("op") or ""
        st  = state[ticker]
        st["monedas"].add(moneda)
        st["n_movimientos"] += 1
        if mep_missing:
            st["fechas_sin_mep"].add(fecha)

        try:
            qty = abs(float(b.get("cantidad") or 0))
        except (TypeError, ValueError):
            qty = 0.0

        if cat in _CATS_PAGO:
            st["cash_pagado"]    += abs(imp_ars)
            st["qty_compras"]    += qty
        elif cat in _CATS_COBRO_VENTA:
            st["cash_cobrado_venta"] += imp_ars
            st["qty_ventas"]         += qty
        elif cat in _CATS_COBRO_PASIVO:
            st["cash_cobrado_pasivo"] += imp_ars
            if op in st["breakdown_pasivo"]:
                st["breakdown_pasivo"][op] += imp_ars
            else:
                st["breakdown_otros"] += imp_ars

    # ── 4. Posición actual desde Valuaciones.AuM (último snapshot) ──
    last = db_v["AuM"].find_one(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "fecha_snapshot": 1},
        sort=[("fecha_snapshot", -1)],
    )
    fecha_actual = last["fecha_snapshot"] if last else None
    aum_por_ticker: dict[str, dict] = {}
    if fecha_actual:
        for d in db_v["AuM"].find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha_actual},
            {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1, "valuacion": 1},
        ):
            unidad = d.get("unidad", "")
            ticker = _ticker_corto(unidad)
            if not ticker:
                continue
            aum_por_ticker[ticker] = {
                "unidad":    unidad,
                "cantidad":  float(d.get("cantidad") or 0),
                "precio":    float(d.get("precio") or 0),
                "valuacion": float(d.get("valuacion") or 0),
            }

    # ── 5. Armar rows + totales ──────────────────────────────────────
    rows: list[dict[str, Any]] = []
    tot = {
        "cash_pagado":         0.0,
        "cash_cobrado_venta":  0.0,
        "cash_cobrado_pasivo": 0.0,
        "valor_actual":        0.0,
        "pnl_total":           0.0,
    }

    # Unión de tickers que aparecen en boletos OR en posición actual
    # (un ticker puede estar en AuM sin boletos relevantes — pre-data,
    # transferencia interna, etc.).
    todos_tickers = set(state) | set(aum_por_ticker)

    for ticker in todos_tickers:
        st = state.get(ticker) or _new_state()
        aum = aum_por_ticker.get(ticker, {})
        valor_actual = float(aum.get("valuacion") or 0)
        qty_actual   = float(aum.get("cantidad") or 0)

        cash_neto = (
            -st["cash_pagado"]
            + st["cash_cobrado_venta"]
            + st["cash_cobrado_pasivo"]
        )
        pnl_total = valor_actual + cash_neto
        pnl_pct   = (pnl_total / st["cash_pagado"]) * 100 if st["cash_pagado"] > 0 else None

        qty_neta = st["qty_compras"] - st["qty_ventas"]
        if st["cash_pagado"] == 0 and st["cash_cobrado_venta"] == 0 and qty_actual > 0:
            completeness = "sin_boletos"  # posición pre-data, no hay info de costo
        elif abs(qty_neta - qty_actual) < 0.01:
            completeness = "completa"
        else:
            completeness = "parcial"

        breakdown = {k: round(v, 2) for k, v in st["breakdown_pasivo"].items() if v != 0}
        if st["breakdown_otros"] != 0:
            breakdown["Otros"] = round(st["breakdown_otros"], 2)

        rows.append({
            "ticker":              ticker,
            "unidad":              aum.get("unidad", ""),
            "cantidad_actual":     round(qty_actual, 4),
            "valor_actual":        round(valor_actual, 2),
            "cash_pagado":         round(st["cash_pagado"], 2),
            "cash_cobrado_venta":  round(st["cash_cobrado_venta"], 2),
            "cash_cobrado_pasivo": round(st["cash_cobrado_pasivo"], 2),
            "breakdown_pasivo":    breakdown,
            "pnl_total":           round(pnl_total, 2),
            "pnl_pct":             round(pnl_pct, 2) if pnl_pct is not None else None,
            "completeness":        completeness,
            "moneda_mixta":        len(st["monedas"]) > 1,
            "n_movimientos":       st["n_movimientos"],
            "fechas_sin_mep":      sorted(st["fechas_sin_mep"]),
        })

        tot["cash_pagado"]         += st["cash_pagado"]
        tot["cash_cobrado_venta"]  += st["cash_cobrado_venta"]
        tot["cash_cobrado_pasivo"] += st["cash_cobrado_pasivo"]
        tot["valor_actual"]        += valor_actual
        tot["pnl_total"]           += pnl_total

    rows.sort(key=lambda r: -r["pnl_total"])
    pnl_pct_total = (tot["pnl_total"] / tot["cash_pagado"]) * 100 if tot["cash_pagado"] > 0 else None

    return {
        "id_cuenta":    id_cuenta,
        "fecha_actual": fecha_actual,
        "rows":         rows,
        "totales": {
            "cash_pagado":         round(tot["cash_pagado"], 2),
            "cash_cobrado_venta":  round(tot["cash_cobrado_venta"], 2),
            "cash_cobrado_pasivo": round(tot["cash_cobrado_pasivo"], 2),
            "valor_actual":        round(tot["valor_actual"], 2),
            "pnl_total":           round(tot["pnl_total"], 2),
            "pnl_pct":             round(pnl_pct_total, 2) if pnl_pct_total is not None else None,
        },
        "n_tickers": len(rows),
    }
