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
from api.db import (
    get_db_cashflow,
    get_db_titulos,
    get_db_trading,
    get_db_valuaciones,
)

logger = logging.getLogger("api.valuaciones")

# ── Constantes compartidas ───────────────────────────────────────────────

# Categorías que cuentan como flujos externos (no son rebalanceo dentro
# del portfolio — verdadero "money in / money out" de la cuenta).
_FLUJO_EXTERNO_DEPOSITO = {"deposito", "transferencia"}
_FLUJO_EXTERNO_EXTRACCION = {"extraccion"}
_FLUJOS_EXTERNOS_ALL = _FLUJO_EXTERNO_DEPOSITO | _FLUJO_EXTERNO_EXTRACCION

# Monedas que necesitan pesificación al MEP del día. ARS se queda como
# está. Resto se asume valor en USD-equivalente y se multiplica por el
# MEP de la fecha del movimiento.
_MONEDAS_USD_EQUIV: set[str] = {"USD", "USDC", "USDL"}


def _get_mep_for_date(fecha_iso: str, db_val) -> float | None:
    """Devuelve el último MEP <= end-of-day(fecha_iso) desde
    Valuaciones.Dolar. Si la fecha cae en finde/feriado o no hay doc
    para esa fecha exacta, cae al último anterior — el MEP no se mueve
    los días no hábiles, así que es la mejor proxy.

    Returns None si no hay ningún MEP en la base.
    """
    from datetime import datetime as _dt
    try:
        target = _dt.fromisoformat(fecha_iso + "T23:59:59")
    except ValueError:
        return None
    doc = db_val["Dolar"].find_one(
        {"mep": {"$ne": None}, "timestamp": {"$lte": target}},
        sort=[("timestamp", -1)],
    )
    if not doc:
        return None
    try:
        return float(doc.get("mep") or 0) or None
    except (TypeError, ValueError):
        return None


def _pesificar(importe: float, moneda: str | None, mep: float | None) -> float:
    """Convierte importe a ARS según moneda + MEP. Si moneda es ARS o
    None → devuelve igual. Si es USD/USDC/USDL → multiplica por MEP.
    Si no hay MEP disponible (caso edge) → devuelve importe original
    sin convertir (mejor que cero — al menos no se pierde el dato)."""
    if not moneda or moneda == "ARS":
        return importe
    if moneda in _MONEDAS_USD_EQUIV and mep is not None:
        return importe * mep
    # Moneda desconocida o sin MEP → as-is con warning de log.
    logger.warning(
        "_pesificar: moneda=%r sin conversión, importe=%r quedó nominal",
        moneda, importe,
    )
    return importe

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

    # 2. Flujos externos — pesificados al MEP de la fecha de cada movimiento.
    # Se hace en Python (no $group server-side) porque la tasa MEP es
    # per-fecha del MOVIMIENTO, no por mes. Cada doc se convierte a ARS
    # antes de sumar al bucket de su mes.
    movimientos_raw = list(db_cf["NegocioMovimientos"].find(
        {
            "cuenta":    {"$regex": f"^\\[{id_cuenta}\\]"},
            "categoria": {"$in": list(_FLUJOS_EXTERNOS_ALL)},
        },
        {"_id": 0, "fecha": 1, "categoria": 1, "importe": 1, "moneda": 1},
    ))
    # Cache MEP por fecha — evita re-queries dentro del mismo mes.
    mep_cache: dict[str, float | None] = {}
    flujos_by_mes: dict[str, dict[str, float]] = {}
    for m in movimientos_raw:
        fecha = m.get("fecha")
        if not fecha or not isinstance(fecha, str):
            continue
        try:
            imp_orig = float(m.get("importe") or 0)
        except (TypeError, ValueError):
            continue
        moneda = m.get("moneda") or "ARS"
        if moneda != "ARS" and fecha not in mep_cache:
            mep_cache[fecha] = _get_mep_for_date(fecha, db_val)
        mep = mep_cache.get(fecha)
        imp_ars = _pesificar(imp_orig, moneda, mep)
        mes = fecha[:7]
        bucket = flujos_by_mes.setdefault(mes, {"depositos": 0.0, "extracciones": 0.0})
        cat = m.get("categoria")
        if cat in _FLUJO_EXTERNO_DEPOSITO:
            bucket["depositos"] += imp_ars
        elif cat in _FLUJO_EXTERNO_EXTRACCION:
            bucket["extracciones"] += imp_ars

    # 3. Merge y compute deltas REALES (excluyendo flujo neto pesificado).
    # delta_bruto = cierre_t - cierre_{t-1}    (cambio observado en el saldo, ARS)
    # delta_real  = delta_bruto - flujo_neto   (performance real de inversiones,
    #                                            aislando depósitos y extracciones
    #                                            ya convertidos a ARS)
    rows: list[dict[str, Any]] = []
    prev_val: float | None = None
    for c in cierres:
        mes = c["_id"]
        f = flujos_by_mes.get(mes, {})
        depositos = float(f.get("depositos") or 0)
        extracciones = float(f.get("extracciones") or 0)
        flujo_neto = depositos + extracciones  # extracciones suelen venir negativas
        # Si el feed no normaliza el signo, fallback:
        if extracciones > 0:
            flujo_neto = depositos - extracciones
        cierre = float(c.get("valuacion_cierre") or 0)
        delta_bruto = (cierre - prev_val) if prev_val is not None else None
        delta_real = (
            (delta_bruto - flujo_neto) if delta_bruto is not None else None
        )
        rows.append({
            "mes":              mes,
            "ultimo_dia":       c.get("ultimo_dia"),
            "valuacion_cierre": round(cierre, 2),
            "depositos":        round(depositos, 2),
            "extracciones":     round(extracciones, 2),
            "flujo_neto":       round(flujo_neto, 2),
            "delta_bruto":      round(delta_bruto, 2) if delta_bruto is not None else None,
            "delta_real":       round(delta_real, 2) if delta_real is not None else None,
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


@cached(ttl=60)
def posiciones_actuales(
    id_cuenta: str, fecha: str | None = None,
) -> dict[str, Any]:
    """Posiciones de un fecha_snapshot dado para la cuenta.

    Read directo de Valuaciones.AuM (sin cost basis ni boletos): si
    `fecha` es None, usa el snapshot más reciente. Si se pasa una
    fecha (YYYY-MM-DD), usa exactamente esa. Devuelve la lista de
    unidades con cantidad, precio, valuación y share del total.

    Sirve como "snapshot" en el panel derecho de /valuaciones, con
    selector de fecha para ver posiciones históricas (clickear una
    fila de la tabla mensual cambia la fecha mostrada).
    """
    db_val = get_db_valuaciones()

    if fecha:
        # Validar que efectivamente exista esa fecha para la cuenta.
        # Si no existe, devolver vacío en lugar de mentir con la latest.
        exists = db_val["AuM"].count_documents(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha},
            limit=1,
        )
        if not exists:
            return {
                "id_cuenta": id_cuenta, "fecha": fecha,
                "posiciones": [], "total": 0.0, "n": 0,
            }
    else:
        # Default: latest fecha_snapshot para esta cuenta.
        latest = list(
            db_val["AuM"]
            .find({"id_cuenta": id_cuenta}, {"_id": 0, "fecha_snapshot": 1})
            .sort("fecha_snapshot", -1)
            .limit(1)
        )
        if not latest:
            return {
                "id_cuenta": id_cuenta, "fecha": None,
                "posiciones": [], "total": 0.0, "n": 0,
            }
        fecha = latest[0]["fecha_snapshot"]

    docs = list(
        db_val["AuM"]
        .find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha},
            {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1,
             "valuacion": 1, "tipoTitulo": 1},
        )
        .sort("valuacion", -1)
    )

    # Agregar por unidad — varios docs con la misma unidad pueden existir
    # si la cuenta tiene múltiples lotes / movimientos del día.
    by_unidad: dict[str, dict[str, Any]] = {}
    for d in docs:
        unidad = d.get("unidad")
        if not unidad:
            continue
        try:
            qty = float(d.get("cantidad") or 0)
            precio = float(d.get("precio") or 0)
            val = float(d.get("valuacion") or 0)
        except (TypeError, ValueError):
            continue
        st = by_unidad.setdefault(unidad, {
            "ticker":    unidad,
            "cantidad":  0.0,
            "precio":    precio,
            "valuacion": 0.0,
            "tipo":      d.get("tipoTitulo"),
        })
        st["cantidad"]  += qty
        st["valuacion"] += val
        # precio se sobrescribe — todos los lotes del mismo día tienen mismo precio.
        st["precio"] = precio

    # Enriquecer con `cartera` desde TitulosAPI.AssetsAPI.
    # AuM no persiste cartera en el doc — vive en master data, joineado
    # por `unidad`. Sin esto la UI no puede separar ARS / DL / HD / FCI.
    db_t = get_db_titulos()
    unidades = list(by_unidad.keys())
    cartera_by_unidad: dict[str, str] = {}
    if unidades:
        cur = db_t["AssetsAPI"].find(
            {"unidad": {"$in": unidades}},
            {"_id": 0, "unidad": 1, "cartera": 1},
        )
        for a in cur:
            u = a.get("unidad")
            c = a.get("cartera") or ""
            if u:
                cartera_by_unidad[u] = c

    # Sort por valuación con SIGNO descendente — longs arriba, shorts/cash
    # negativo abajo. Antes era por |valuacion| que mezclaba shorts grandes
    # con longs grandes, confundiendo la lectura.
    rows = sorted(by_unidad.values(), key=lambda r: -r["valuacion"])
    total = sum(r["valuacion"] for r in rows)
    return {
        "id_cuenta": id_cuenta,
        "fecha":     fecha,
        "posiciones": [
            {
                "ticker":    r["ticker"],
                "tipo":      str(r["tipo"]) if r["tipo"] not in (None, "") else None,
                "cartera":   cartera_by_unidad.get(r["ticker"]) or "",
                "cantidad":  round(r["cantidad"], 4),
                "precio":    round(r["precio"], 4),
                "valuacion": round(r["valuacion"], 2),
                "share":     (
                    round((r["valuacion"] / total) * 100, 2)
                    if total else None
                ),
            }
            for r in rows
        ],
        "total": round(total, 2),
        "n":     len(rows),
    }


@cached(ttl=300)
def movimientos_mes(id_cuenta: str, fecha_anchor: str) -> dict[str, Any]:
    """Movimientos individuales (depósitos / extracciones / transferencias)
    para una cuenta en el mes que contiene `fecha_anchor`.

    Para el panel de auditoría en /valuaciones — al clickear un mes en la
    tabla mensual, este endpoint devuelve cada boleto del mes con su
    fecha real, importe y descripción para que el manager audite los
    flujos uno por uno (vs solo ver el neto agregado del mes).

    Args:
        id_cuenta: numérico, ej "805".
        fecha_anchor: YYYY-MM-DD — define el mes a consultar (mes y año).

    Returns:
        {
          mes: "YYYY-MM",
          movimientos: [{fecha, comprobante, categoria, importe, moneda,
                         op, ticker, informacion, cuenta}, ...],
          n, total_neto, total_depositos, total_extracciones
        }
    """
    db_cf = get_db_cashflow()
    db_val = get_db_valuaciones()
    mes = fecha_anchor[:7]  # YYYY-MM

    # Match: cuenta por prefijo numérico, fecha contiene el mes target,
    # categoria entre los flujos externos.
    match = {
        "cuenta":    {"$regex": f"^\[{id_cuenta}\]"},
        "categoria": {"$in": list(_FLUJOS_EXTERNOS_ALL)},
        "fecha":     {"$regex": f"^{mes}"},
    }
    docs = list(
        db_cf["NegocioMovimientos"]
        .find(
            match,
            {"_id": 0, "fecha": 1, "comprobante": 1, "categoria": 1,
             "importe": 1, "moneda": 1, "op": 1, "ticker": 1,
             "informacion": 1, "cuenta": 1},
        )
        .sort([("fecha", 1), ("comprobante", 1)])
    )

    # Cache MEP por fecha (varios movimientos del mismo día comparten tasa).
    mep_cache: dict[str, float | None] = {}
    total_dep_ars = 0.0
    total_ext_ars = 0.0
    movimientos: list[dict[str, Any]] = []
    for d in docs:
        fecha = d.get("fecha") or ""
        cat = d.get("categoria")
        try:
            imp_orig = float(d.get("importe") or 0)
        except (TypeError, ValueError):
            imp_orig = 0.0
        moneda = d.get("moneda") or "ARS"
        if moneda != "ARS" and fecha and fecha not in mep_cache:
            mep_cache[fecha] = _get_mep_for_date(fecha, db_val)
        mep = mep_cache.get(fecha)
        imp_ars = _pesificar(imp_orig, moneda, mep)

        if cat in _FLUJO_EXTERNO_DEPOSITO:
            total_dep_ars += imp_ars
        elif cat in _FLUJO_EXTERNO_EXTRACCION:
            total_ext_ars += imp_ars

        movimientos.append({
            "fecha":       fecha,
            "comprobante": d.get("comprobante"),
            "categoria":   cat,
            "importe":     round(imp_orig, 2),
            "importe_ars": round(imp_ars, 2),
            "mep_rate":    round(mep, 2) if mep is not None else None,
            "moneda":      moneda,
            "op":          d.get("op"),
            "ticker":      d.get("ticker"),
            "informacion": d.get("informacion"),
            "cuenta":      d.get("cuenta"),
        })

    # Net flow ARS: extracciones suelen venir negativas — si no, se normaliza.
    total_neto_ars = total_dep_ars + total_ext_ars
    if total_ext_ars > 0:
        total_neto_ars = total_dep_ars - total_ext_ars

    return {
        "id_cuenta":         id_cuenta,
        "mes":               mes,
        "movimientos":       movimientos,
        "n":                 len(movimientos),
        # Totales ya en ARS (cada movimiento USD/USDC/USDL se convierte
        # con el MEP del día antes de sumar).
        "total_depositos":   round(total_dep_ars, 2),
        "total_extracciones": round(total_ext_ars, 2),
        "total_neto":        round(total_neto_ars, 2),
    }
