"""Valuaciones — performance e historia por cuenta.

Dos enfoques convivientes:

(A) AUM-BASED [usado por /serie y /mensual]
    Lee `Valuaciones.AuM` directo — es el snapshot diario MTM canónico
    armado por jobs/aum.py (con normalizaciones por tipo: /100 para
    renta fija, etc). Para cada (id_cuenta, fecha_snapshot) sumamos
    `valuacion` de todas las posiciones para obtener el portfolio total
    en ARS de ese día. Combinado con flujos externos (depósitos /
    extracciones de CashFlow.NegocioMovimientos) da la mensualización
    "valor de cierre + flujo neto" que pide la vista.

(B) COST-BASIS LEDGER [usado por /posiciones]
    Reconstruye lots de boletos compra/venta con weighted-average cost.
    Útil para PnL realizado vs no realizado por ticker, pero limitado
    cuando hay posiciones anteriores al primer boleto disponible. Se
    mantiene para drill-down per-ticker en Phase 2.
"""
from __future__ import annotations

import logging
from typing import Any

from api.cache import cached
from api.db import get_db_cashflow, get_db_trading, get_db_valuaciones

logger = logging.getLogger("api.valuaciones")

# ── Constantes compartidas ───────────────────────────────────────────────

# Categorías que cuentan como flujos externos (no son rebalanceo dentro
# del portfolio — verdadero "money in / money out" de la cuenta).
_FLUJO_EXTERNO_DEPOSITO = {"deposito", "transferencia"}
_FLUJO_EXTERNO_EXTRACCION = {"extraccion"}
_FLUJOS_EXTERNOS_ALL = _FLUJO_EXTERNO_DEPOSITO | _FLUJO_EXTERNO_EXTRACCION

# Categorías de boleto que afectan cost basis. FCI super (suscripcion_fci)
# se trata como una compra de un activo (la cuotaparte). Sus rescates,
# como una venta. Cauciones / depositos / extracciones no entran al
# cost basis — son flows externos.
_COMPRA_CATS: set[str] = {"compra", "suscripcion_fci"}
_VENTA_CATS: set[str] = {"venta", "rescate_fci"}
_CAT_ALL: list[str] = sorted(_COMPRA_CATS | _VENTA_CATS)


def _last_market_price(metrics: dict | None) -> float | None:
    """Mejor precio disponible: live > cierre > None."""
    if not metrics:
        return None
    p = metrics.get("last_price")
    if p is not None:
        return float(p)
    p = metrics.get("closing_price")
    return float(p) if p is not None else None


@cached(ttl=60)
def posiciones_cuenta(id_cuenta: str, hasta: str | None = None) -> dict[str, Any]:
    """Posiciones actuales con cost basis weighted-average para una cuenta.

    Args:
        id_cuenta: prefijo numérico (ej "805"). Matchea cuenta del boleto
            por regex `^\\[805\\]`.
        hasta: YYYY-MM-DD inclusive. None = sin límite (todos los boletos).

    Returns:
        {
          id_cuenta, hasta,
          posiciones: [{ticker, cantidad, precio_promedio, precio_actual,
                        valor_mercado, costo_total, pnl_no_realizado,
                        pnl_no_realizado_pct, pnl_realizado, moneda,
                        n_compras, n_ventas, completeness}, ...],
          totales: {costo_total, valor_mercado, pnl_no_realizado,
                    pnl_realizado, pnl_total, pnl_total_pct},
          n_tickers, n_boletos
        }
    """
    db_cf = get_db_cashflow()
    db_t = get_db_trading()

    # 1. Boletos relevantes — solo categorías que mueven cost basis.
    match: dict[str, Any] = {
        "cuenta":    {"$regex": f"^\\[{id_cuenta}\\]"},
        "categoria": {"$in": _CAT_ALL},
        "ticker":    {"$ne": None},
    }
    if hasta:
        match["fecha"] = {"$lte": hasta}

    boletos = list(
        db_cf["NegocioMovimientos"]
        .find(
            match,
            {"_id": 0, "fecha": 1, "ticker": 1, "categoria": 1,
             "cantidad": 1, "precio": 1, "moneda": 1, "comprobante": 1},
        )
        .sort([("fecha", 1), ("comprobante", 1)])
    )

    # 2. Acumular por ticker — orden temporal estricto para weighted avg.
    state: dict[str, dict[str, Any]] = {}
    for b in boletos:
        ticker = b.get("ticker")
        if not ticker:
            continue
        try:
            qty = abs(float(b.get("cantidad") or 0))
            precio = float(b.get("precio") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or precio <= 0:
            continue

        st = state.setdefault(ticker, {
            "qty":           0.0,
            "total_cost":    0.0,
            "realized_pnl":  0.0,
            "moneda":        b.get("moneda") or "ARS",
            "n_compras":     0,
            "n_ventas":      0,
            "qty_compras":   0.0,  # bruto, para indicador de completeness
        })
        cat = b.get("categoria")
        if cat in _COMPRA_CATS:
            st["total_cost"]  += precio * qty
            st["qty"]         += qty
            st["qty_compras"] += qty
            st["n_compras"]   += 1
        elif cat in _VENTA_CATS:
            avg_cost = st["total_cost"] / st["qty"] if st["qty"] > 0 else 0.0
            qty_to_sell = min(qty, st["qty"])
            if qty_to_sell > 0:
                st["realized_pnl"] += (precio - avg_cost) * qty_to_sell
                st["total_cost"]   -= avg_cost * qty_to_sell
                st["qty"]          -= qty_to_sell
            st["n_ventas"] += 1

    if not state:
        return {
            "id_cuenta": id_cuenta, "hasta": hasta,
            "posiciones": [],
            "totales": {
                "costo_total":      0.0,
                "valor_mercado":    0.0,
                "pnl_no_realizado": 0.0,
                "pnl_realizado":    0.0,
                "pnl_total":        0.0,
                "pnl_total_pct":    None,
            },
            "n_tickers": 0, "n_boletos": len(boletos),
        }

    # 3. Map short → long ticker via Trading.Curvas (ticker_corto → ticker).
    short_tickers = list(state.keys())
    curvas = list(db_t["Curvas"].find(
        {"ticker_corto": {"$in": short_tickers}},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1, "fecha_vencimiento": 1},
    ))
    short_to_curva: dict[str, dict] = {
        c["ticker_corto"]: c for c in curvas if c.get("ticker_corto")
    }

    # 4. Prices live de MarketSnapshot (long ticker is the _id).
    long_tickers = [c["ticker"] for c in short_to_curva.values() if c.get("ticker")]
    snapshots: dict[str, dict] = {}
    if long_tickers:
        for d in db_t["MarketSnapshot"].find(
            {"_id": {"$in": long_tickers}},
            {"_id": 1, "metrics": 1},
        ):
            snapshots[d["_id"]] = d

    # 5. Build response rows.
    rows: list[dict[str, Any]] = []
    total_market_value = 0.0
    total_cost = 0.0
    total_realized = 0.0
    total_unrealized = 0.0

    for short_ticker, st in state.items():
        if st["qty"] <= 0 and st["realized_pnl"] == 0:
            continue

        curva = short_to_curva.get(short_ticker, {})
        long_ticker = curva.get("ticker")
        snap = snapshots.get(long_ticker, {}) if long_ticker else {}
        precio_actual = _last_market_price(snap.get("metrics"))

        avg_cost = st["total_cost"] / st["qty"] if st["qty"] > 0 else 0.0
        valor_mercado = (precio_actual or 0.0) * st["qty"]
        unrealized = valor_mercado - st["total_cost"]
        unrealized_pct = (
            unrealized / st["total_cost"] * 100
            if st["total_cost"] > 0 else None
        )

        # Completeness: si qty_compras < qty_actual seguro faltan compras
        # previas al período. (Otra señal: realized_pnl = 0 y solo ventas.)
        completeness = "parcial" if st["qty"] > st["qty_compras"] else "completa"

        rows.append({
            "ticker":               short_ticker,
            "tipo":                 curva.get("tipo"),
            "fecha_vencimiento":    str(curva.get("fecha_vencimiento") or "")[:10] or None,
            "moneda":               st["moneda"],
            "cantidad":             round(st["qty"], 4),
            "precio_promedio":      round(avg_cost, 4),
            "precio_actual":        round(precio_actual, 4) if precio_actual is not None else None,
            "costo_total":          round(st["total_cost"], 2),
            "valor_mercado":        round(valor_mercado, 2),
            "pnl_no_realizado":     round(unrealized, 2),
            "pnl_no_realizado_pct": round(unrealized_pct, 2) if unrealized_pct is not None else None,
            "pnl_realizado":        round(st["realized_pnl"], 2),
            "n_compras":            st["n_compras"],
            "n_ventas":             st["n_ventas"],
            "completeness":         completeness,
        })

        total_market_value += valor_mercado
        total_cost         += st["total_cost"]
        total_realized     += st["realized_pnl"]
        total_unrealized   += unrealized

    rows.sort(key=lambda r: -r["valor_mercado"])

    pnl_total = total_realized + total_unrealized
    return {
        "id_cuenta":  id_cuenta,
        "hasta":      hasta,
        "posiciones": rows,
        "totales": {
            "costo_total":      round(total_cost, 2),
            "valor_mercado":    round(total_market_value, 2),
            "pnl_no_realizado": round(total_unrealized, 2),
            "pnl_realizado":    round(total_realized, 2),
            "pnl_total":        round(pnl_total, 2),
            "pnl_total_pct":    round(pnl_total / total_cost * 100, 2) if total_cost > 0 else None,
        },
        "n_tickers":  len(rows),
        "n_boletos":  len(boletos),
    }


# ─────────────────────────────────────────────────────────────────────────
# AUM-based: portfolio total diario + cierres mensuales + flujos externos.
# ─────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def serie_valor_cuenta(
    id_cuenta: str,
    desde: str | None = None,
    hasta: str | None = None,
) -> dict[str, Any]:
    """Serie diaria del portfolio total para una cuenta.

    Suma `valuacion` (ya normalizada por jobs/aum.py — ARS) de todas las
    posiciones por (id_cuenta, fecha_snapshot). Devuelve una fila por día
    con valor total + n posiciones contribuyendo.

    Args:
        id_cuenta: id numérico, ej "805".
        desde / hasta: YYYY-MM-DD inclusive. None = sin límite.
    """
    db_val = get_db_valuaciones()
    match: dict[str, Any] = {"id_cuenta": id_cuenta}
    rango: dict[str, str] = {}
    if desde:
        rango["$gte"] = desde
    if hasta:
        rango["$lte"] = hasta
    if rango:
        match["fecha_snapshot"] = rango

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":       "$fecha_snapshot",
            "valuacion": {"$sum": "$valuacion"},
            "n":         {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id":       0,
            "fecha":     "$_id",
            "valuacion": {"$round": ["$valuacion", 2]},
            "n":         1,
        }},
    ]
    serie = list(db_val["AuM"].aggregate(pipeline))
    return {
        "id_cuenta": id_cuenta,
        "desde":     desde,
        "hasta":     hasta,
        "serie":     serie,
        "ultimo": serie[-1] if serie else None,
        "primero": serie[0] if serie else None,
    }


@cached(ttl=300)
def valuacion_mensual(id_cuenta: str) -> dict[str, Any]:
    """Tabla mensual: valor al cierre del mes + flujos externos del mes.

    El "cierre" del mes es el valor del último fecha_snapshot disponible
    en ese mes (puede ser el último día hábil — no necesariamente el
    día 30). Los flujos externos suman depósitos/transferencias y restan
    extracciones de CashFlow.NegocioMovimientos para los días del mes.

    Returns:
      [{
        mes: "YYYY-MM",
        ultimo_dia: "YYYY-MM-DD",
        valuacion_cierre: float,
        depositos: float,
        extracciones: float,
        flujo_neto: float,
        delta_valuacion: float | None,  # cierre actual − cierre anterior
        n_posiciones: int,
      }, ...]
    """
    db_val = get_db_valuaciones()
    db_cf = get_db_cashflow()

    # 1. Valuación al cierre de cada mes (último fecha_snapshot del mes).
    pipeline_aum = [
        {"$match": {"id_cuenta": id_cuenta}},
        {"$group": {
            "_id":       "$fecha_snapshot",
            "valuacion": {"$sum": "$valuacion"},
            "n":         {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
        # Re-group por mes: take last day's value.
        {"$group": {
            "_id":              {"$substr": ["$_id", 0, 7]},
            "ultimo_dia":       {"$last": "$_id"},
            "valuacion_cierre": {"$last": "$valuacion"},
            "n_posiciones":     {"$last": "$n"},
        }},
        {"$sort": {"_id": 1}},  # ascendente para calcular delta
    ]
    cierres = list(db_val["AuM"].aggregate(pipeline_aum))

    # 2. Flujos externos por mes (depositos / extracciones de la cuenta).
    pipeline_flujos = [
        {"$match": {
            "cuenta":    {"$regex": f"^\[{id_cuenta}\]"},
            "categoria": {"$in": list(_FLUJOS_EXTERNOS_ALL)},
        }},
        {"$group": {
            "_id": {"$substr": ["$fecha", 0, 7]},
            "depositos": {"$sum": {"$cond": [
                {"$in": ["$categoria", list(_FLUJO_EXTERNO_DEPOSITO)]},
                {"$ifNull": ["$importe", 0]},
                0,
            ]}},
            "extracciones": {"$sum": {"$cond": [
                {"$in": ["$categoria", list(_FLUJO_EXTERNO_EXTRACCION)]},
                {"$ifNull": ["$importe", 0]},
                0,
            ]}},
        }},
    ]
    flujos_by_mes: dict[str, dict] = {
        r["_id"]: r for r in db_cf["NegocioMovimientos"].aggregate(pipeline_flujos)
    }

    # 3. Merge y compute deltas.
    rows: list[dict[str, Any]] = []
    prev_val: float | None = None
    for c in cierres:
        mes = c["_id"]
        f = flujos_by_mes.get(mes, {})
        depositos = float(f.get("depositos") or 0)
        extracciones = float(f.get("extracciones") or 0)
        flujo_neto = depositos + extracciones  # extracciones suelen venir negativas
        # Si el sign de extracciones no viene negativo del feed, normalizamos:
        if extracciones > 0:
            flujo_neto = depositos - extracciones
        cierre = float(c.get("valuacion_cierre") or 0)
        delta = (cierre - prev_val) if prev_val is not None else None
        rows.append({
            "mes":              mes,
            "ultimo_dia":       c.get("ultimo_dia"),
            "valuacion_cierre": round(cierre, 2),
            "depositos":        round(depositos, 2),
            "extracciones":     round(extracciones, 2),
            "flujo_neto":       round(flujo_neto, 2),
            "delta_valuacion":  round(delta, 2) if delta is not None else None,
            "n_posiciones":     c.get("n_posiciones", 0),
        })
        prev_val = cierre

    # Devolvemos en orden descendente (mes más reciente primero — para UI).
    rows.reverse()
    return {
        "id_cuenta": id_cuenta,
        "meses":     rows,
        "n_meses":   len(rows),
    }
