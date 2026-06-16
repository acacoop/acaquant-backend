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
from datetime import date as _date
from typing import Any

from api.cache import cached
from api.db import (
    get_db_trading,
    get_db_valuaciones,
)
from api.services._negocio_sql_read import negocio_movimientos_rows
from api.services.assets_sql import assets_rows
from quant.xirr import xirr as _xirr

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


def _get_mep_for_date(fecha_iso: str, db_val=None) -> float | None:
    """MEP histórico — delega en la implementación ÚNICA (_mep.get_mep_for_date).

    Acá vivía una copia idéntica (AUDITORIA M2): un fix en una dejaba a la
    otra desfasada en cálculo de plata. `db_val` se conserva en la firma por
    compatibilidad de los call sites pero se ignora — la fuente es siempre
    Valuaciones.Dolar vía get_db_valuaciones() (mismo origen que el db_val
    que pasaban los callers).
    """
    from api.services._mep import get_mep_for_date
    return get_mep_for_date(fecha_iso)


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
    db_t = get_db_trading()

    # 1. Boletos relevantes — solo categorías que mueven cost basis.
    # SQL operaciones.negocio_movimientos (shape-Mongo vía shim).
    boletos = negocio_movimientos_rows(
        fields=("fecha", "ticker", "categoria", "cantidad", "precio", "moneda", "comprobante"),
        id_cuenta=str(id_cuenta),
        categorias=_CAT_ALL,
        ticker_not_null=True,
        fecha_lte=hasta or None,
        order=True,
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


def _cierres_fecha_data(id_cuenta: str, cartera: str | None, engine: str) -> list[dict]:
    """Cierres por fecha_snapshot → [{_id, valuacion, n}] ordenado asc. Fuente SWAPPABLE:
    Mongo `Valuaciones.AuM` (default) o SQL `portafolio.tenencia` (engine='sql', dato con
    fechas corregidas). El resto del cálculo mensual (flujos/MEP/XIRR/TWR) NO cambia —
    opera sobre esta lista, sea cual sea la fuente."""
    if engine == "sql":
        from api.services import valuaciones_sql as _vsql
        return _vsql.cierres_fecha_data(id_cuenta, cartera)
    pipeline = [
        {"$match": {"id_cuenta": id_cuenta, **({"CARTERA": cartera} if cartera else {})}},
        {"$group": {"_id": "$fecha_snapshot", "valuacion": {"$sum": "$valuacion"},
                    "n": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    return list(get_db_valuaciones()["AuM"].aggregate(pipeline))


@cached(ttl=300)
def valuacion_mensual(id_cuenta: str, engine: str = "mongo") -> dict[str, Any]:
    """Versión CACHEADA (flujo externo: depósitos/extracciones) — la usa Carteras.
    `engine`: 'mongo' (default) o 'sql' (cierres desde portafolio.tenencia corregido).
    La variante con flujo custom (NEGOCIO→Valuaciones) es `_valuacion_mensual`."""
    return _valuacion_mensual(id_cuenta=id_cuenta, engine=engine)


def _valuacion_mensual(id_cuenta: str, flujos_override: dict | None = None,
                       cartera: str | None = None, engine: str = "mongo") -> dict[str, Any]:
    """Tabla mensual: valor al cierre del mes + flujos externos del mes.
    Calcula métricas en ARS y USD paralelas.

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
        delta_bruto: float | None,
        delta_real: float | None,
        tea_mensual: float | None,
        tem_periodo: float | None,
        twr_base100: float,
        # USD parallels:
        mep_cierre: float | None,
        valuacion_cierre_usd: float,
        depositos_usd: float,
        extracciones_usd: float,
        flujo_neto_usd: float,
        delta_bruto_usd: float | None,
        delta_real_usd: float | None,
        tea_mensual_usd: float | None,
        tem_periodo_usd: float | None,
        twr_base100_usd: float,
        n_posiciones: int,
      }, ...]
    """
    db_val = get_db_valuaciones()

    # 1. Valuación al cierre de cada mes (último fecha_snapshot del mes).
    #
    # IMPORTANTE — bucket de cierre vs mes calendario:
    # El snapshot del día 1 de un mes (ej. 2026-03-01) representa la
    # valuación al INICIO del mes corriente == el cierre del MES ANTERIOR.
    # Por eso siempre lo reasignamos al bucket del mes anterior, tanto en
    # modo legacy (1 snap del 1° por mes) como en modo daily (snap del 1°
    # como primer punto del mes). Resultado: la fila "Feb" muestra el
    # cierre del 2026-03-01 (que es el real cierre de Feb), aunque Marzo
    # tenga sus propios daily.
    #
    # Si el día NO es 1, va al bucket de su mes calendar.
    # Sorted asc → el último snapshot que cae en el bucket gana → para
    # meses con daily, gana el del último día hábil; para meses cubiertos
    # solo por el snap del 1° del siguiente, gana ese.
    fechas_data = _cierres_fecha_data(id_cuenta, cartera, engine)

    # Bucket = mes calendario del snapshot. Sorted asc → el último snapshot
    # del mes gana en el dict overwrite. Para meses con backfill EOM
    # (jobs/aum_backfill_historico) gana el snap del 31; para meses con
    # daily completo gana el último día hábil. Los snaps históricos del
    # 1° de mes (régimen viejo) quedan ignorados sin borrar — los pisa el
    # snap del 31 del mismo mes calendario.
    cierres_buckets: dict[str, dict] = {}
    for f in fechas_data:  # sorted asc
        fecha_str = str(f["_id"])
        bucket = fecha_str[:7]
        cierres_buckets[bucket] = {
            "_id":              bucket,
            "ultimo_dia":       fecha_str,
            "valuacion_cierre": f.get("valuacion") or 0,
            "n_posiciones":     f.get("n") or 0,
        }

    cierres = [cierres_buckets[k] for k in sorted(cierres_buckets.keys())]

    # 2. Flujos externos — pesificados al MEP de la fecha de cada movimiento.
    # Se hace en Python (no $group server-side) porque la tasa MEP es
    # per-fecha del MOVIMIENTO, no por mes. Cada doc se convierte a ARS
    # antes de sumar al bucket de su mes.
    # Cache MEP por fecha — evita re-queries dentro del mismo mes.
    mep_cache: dict[str, float | None] = {}
    # `flujos_override`: un caller puede pasar OTRO flujo (mismo shape
    # {mes: {depositos, extracciones, items:[(fecha_iso, imp_ars_signado)]}}) y se
    # usa tal cual — ej. NEGOCIO→Valuaciones usa el neto de los boletos de títulos.
    # El resto del cálculo (XIRR/TEM/TEA/TWR/USD) queda IDÉNTICO a Carteras.
    if flujos_override is not None:
        flujos_by_mes = flujos_override
        movimientos_raw: list[dict] = []
    else:
        movimientos_raw = negocio_movimientos_rows(
            fields=("fecha", "categoria", "importe", "moneda"),
            id_cuenta=str(id_cuenta),
            categorias=list(_FLUJOS_EXTERNOS_ALL),
        )
        flujos_by_mes = {}
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
        bucket = flujos_by_mes.setdefault(
            mes, {"depositos": 0.0, "extracciones": 0.0, "items": []}
        )
        # Importes con signo cliente correcto (+ depósito, - extracción)
        # ya vienen de aunesa_negocio.py. Ignoramos importes nulos para no
        # ensuciar XIRR con flujos = 0 (no aportan info y multiplican iter).
        if imp_ars != 0:
            bucket["items"].append((fecha, imp_ars))
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
    #
    # TEA del mes vía XIRR (TIR.NO.PER de Excel):
    #   Cashflow del mes M:
    #     (ultimo_dia_mes_M-1, +V_cierre_M-1)       valor inicio (positivo)
    #     (fecha_flujo_1,       ±importe_1)         flujos con signo cliente
    #     ...
    #     (ultimo_dia_mes_M,   -V_cierre_M)         valor cierre (negativo)
    #   tea_mensual = xirr(cashflow)               anualizada según convención TIR.NO.PER
    #
    # Base 100:
    #   TEM = (1 + tea_mensual)^(días_mes / 365) − 1   des-anualizada al período
    #   twr_base100 = 100 × Π(1 + TEM_t)                cumulada multiplicativa
    #
    # USD CALCS: Paralelos para dolarización. MEP(fecha_cierre) para valores,
    # MEP(fecha_flujo) para cada flujo. XIRR en USD con cashflows convertidos.
    rows: list[dict[str, Any]] = []
    prev_val: float | None = None
    prev_fecha: str | None = None
    prev_val_usd: float | None = None
    twr_acum: float = 100.0
    twr_acum_usd: float = 100.0
    twr_iniciado = False
    # Cache de MEP para evitar re-queries durante el loop.
    mep_cierre_cache: dict[str, float | None] = {}

    for c in cierres:
        mes = c["_id"]
        f = flujos_by_mes.get(mes, {})
        depositos = float(f.get("depositos") or 0)
        extracciones = float(f.get("extracciones") or 0)
        # `imp_ars` ya trae el signo cliente correcto (+ depósito,
        # - extracción) desde aunesa_negocio.py → `depositos` y
        # `extracciones` son sumas SIGNADAS y el neto es la suma directa.
        # NO hay fallback por signo: `if extracciones > 0: depositos -
        # extracciones` rompía cuando una serie tenía extracciones que
        # netaban positivo (reversas/refunds) → contaminaba delta_real,
        # tea_mensual (XIRR) y twr_base100.
        flujo_neto = depositos + extracciones
        cierre = float(c.get("valuacion_cierre") or 0)
        ultimo_dia = c.get("ultimo_dia")

        # ── MEP del cierre para dolarización ──
        mep_cierre: float | None = None
        if ultimo_dia and ultimo_dia not in mep_cierre_cache:
            mep_cierre_cache[ultimo_dia] = _get_mep_for_date(ultimo_dia, db_val)
        mep_cierre = mep_cierre_cache.get(ultimo_dia)

        # USD conversión de valores
        cierre_usd = cierre / mep_cierre if mep_cierre and mep_cierre > 0 else 0.0

        # USD conversión de flujos — usar MEP de cada fecha del flujo
        depositos_usd = 0.0
        extracciones_usd = 0.0
        for fecha_iso, imp_ars in (f.get("items") or []):
            if fecha_iso not in mep_cache:
                mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso, db_val)
            mep_flujo = mep_cache.get(fecha_iso)
            imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
            # Determinamos si es deposito o extraccion basándonos en el signo
            if imp_ars > 0:
                depositos_usd += imp_usd
            else:
                extracciones_usd += imp_usd
        flujo_neto_usd = depositos_usd + extracciones_usd

        delta_bruto = (cierre - prev_val) if prev_val is not None else None
        delta_real = (
            (delta_bruto - flujo_neto) if delta_bruto is not None else None
        )

        delta_bruto_usd = (cierre_usd - prev_val_usd) if prev_val_usd is not None else None
        delta_real_usd = (
            (delta_bruto_usd - flujo_neto_usd) if delta_bruto_usd is not None else None
        )

        # ── TEA del mes vía XIRR (ARS) ──
        tea_mensual: float | None = None
        r_mes: float | None = None
        if (
            prev_val is not None and prev_val > 0
            and cierre > 0
            and prev_fecha and ultimo_dia
        ):
            try:
                d_inicio = _date.fromisoformat(prev_fecha)
                d_cierre = _date.fromisoformat(ultimo_dia)
                dias_per = max((d_cierre - d_inicio).days, 1)
                guess = (cierre / prev_val) ** (365.0 / dias_per) - 1
                guess = max(min(guess, 50.0), -0.99)
                cashflows: list[tuple[_date, float]] = [(d_inicio, +prev_val)]
                for fecha_iso, imp_ars in (f.get("items") or []):
                    try:
                        cashflows.append((_date.fromisoformat(fecha_iso), float(imp_ars)))
                    except (ValueError, TypeError):
                        continue
                cashflows.append((d_cierre, -cierre))
                tea_mensual = _xirr(cashflows, guess=guess)
                if tea_mensual is not None:
                    dias_periodo = (d_cierre - d_inicio).days
                    if dias_periodo > 0:
                        r_mes = (1 + tea_mensual) ** (dias_periodo / 365) - 1
            except ValueError:
                tea_mensual = None

        # ── TEA del mes vía XIRR (USD) ──
        tea_mensual_usd: float | None = None
        r_mes_usd: float | None = None
        if (
            prev_val_usd is not None and prev_val_usd > 0
            and cierre_usd > 0
            and prev_fecha and ultimo_dia
        ):
            try:
                d_inicio = _date.fromisoformat(prev_fecha)
                d_cierre = _date.fromisoformat(ultimo_dia)
                dias_per = max((d_cierre - d_inicio).days, 1)
                guess = (cierre_usd / prev_val_usd) ** (365.0 / dias_per) - 1
                guess = max(min(guess, 50.0), -0.99)
                cashflows_usd: list[tuple[_date, float]] = [(d_inicio, +prev_val_usd)]
                for fecha_iso, imp_ars in (f.get("items") or []):
                    try:
                        if fecha_iso not in mep_cache:
                            mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso, db_val)
                        mep_flujo = mep_cache.get(fecha_iso)
                        imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
                        if imp_usd != 0:
                            cashflows_usd.append((_date.fromisoformat(fecha_iso), float(imp_usd)))
                    except (ValueError, TypeError):
                        continue
                cashflows_usd.append((d_cierre, -cierre_usd))
                tea_mensual_usd = _xirr(cashflows_usd, guess=guess)
                if tea_mensual_usd is not None:
                    dias_periodo = (d_cierre - d_inicio).days
                    if dias_periodo > 0:
                        r_mes_usd = (1 + tea_mensual_usd) ** (dias_periodo / 365) - 1
            except ValueError:
                tea_mensual_usd = None

        # Anchor del TWR en el primer mes con datos = 100. Después
        # compone con (1 + TEM_t). Si un mes no converge (r_mes None),
        # twr_acum mantiene su último valor (no rompe la serie visual).
        if not twr_iniciado:
            twr_acum = 100.0
            twr_acum_usd = 100.0
            twr_iniciado = True
        elif r_mes is not None:
            twr_acum = twr_acum * (1 + r_mes)
        else:
            # No change if convergence failed
            pass

        if r_mes_usd is not None:
            twr_acum_usd = twr_acum_usd * (1 + r_mes_usd)

        rows.append({
            "mes":                  mes,
            "ultimo_dia":           c.get("ultimo_dia"),
            "valuacion_cierre":     round(cierre, 2),
            "depositos":            round(depositos, 2),
            "extracciones":         round(extracciones, 2),
            "flujo_neto":           round(flujo_neto, 2),
            "delta_bruto":          round(delta_bruto, 2) if delta_bruto is not None else None,
            "delta_real":           round(delta_real, 2) if delta_real is not None else None,
            "tea_mensual":          round(tea_mensual, 6) if tea_mensual is not None else None,
            "tem_periodo":          round(r_mes, 6) if r_mes is not None else None,
            "twr_base100":          round(twr_acum, 4),
            # USD parallels
            "mep_cierre":           round(mep_cierre, 4) if mep_cierre is not None else None,
            "valuacion_cierre_usd": round(cierre_usd, 2),
            "depositos_usd":        round(depositos_usd, 2),
            "extracciones_usd":     round(extracciones_usd, 2),
            "flujo_neto_usd":       round(flujo_neto_usd, 2),
            "delta_bruto_usd":      round(delta_bruto_usd, 2) if delta_bruto_usd is not None else None,
            "delta_real_usd":       round(delta_real_usd, 2) if delta_real_usd is not None else None,
            "tea_mensual_usd":      round(tea_mensual_usd, 6) if tea_mensual_usd is not None else None,
            "tem_periodo_usd":      round(r_mes_usd, 6) if r_mes_usd is not None else None,
            "twr_base100_usd":      round(twr_acum_usd, 4),
            "n_posiciones":         c.get("n_posiciones", 0),
        })
        prev_val = cierre
        prev_val_usd = cierre_usd
        prev_fecha = ultimo_dia

    # Devolvemos en orden descendente (mes más reciente primero — para UI).
    rows.reverse()
    return {
        "id_cuenta": id_cuenta,
        "meses":     rows,
        "n_meses":   len(rows),
    }


def valuacion_mensual_debug(id_cuenta: str, engine: str = "mongo") -> dict[str, Any]:
    """Versión expandida de `valuacion_mensual` para auditoría desde
    /manager. **No cacheada** — devuelve siempre los datos actuales.

    Por cada mes muestra **paso por paso** cómo se llega a la TEA y la
    base 100 en ARS y USD paralelos:

      - fecha_inicio / fecha_cierre del período (último día con snapshot)
      - valor_inicio / valor_cierre (saldo del portfolio)
      - flujos_individuales: cada doc de NegocioMovimientos del mes con
          fecha, categoria, op, ticker, moneda, importe_original, MEP
          aplicado e importe_ars resultante.
      - cashflow_xirr: lista exacta de (fecha, monto) que recibe la
          función xirr() — útil para reproducir el cálculo en Excel
          con TIR.NO.PER.
      - cashflow_xirr_usd: idem en USD.
      - tea_mensual / tea_mensual_usd: resultado de XIRR (TEA anualizada).
      - dias_periodo, tem_periodo, tem_periodo_usd: TEA des-anualizada al período.
      - twr_base100_acum / twr_base100_acum_usd: base 100 cumulada.
      - delta_bruto, delta_real, flujo_neto, depositos, extracciones (ARS y USD).

    Args:
        id_cuenta: id numérico, ej "805".

    Returns:
        {
          id_cuenta, meses: [{...mes_detallado_ars_usd}, ...], n_meses,
          resumen: {primer_mes, ultimo_mes, twr_final, twr_final_usd, ganancia_pct, ganancia_pct_usd}
        }
    """
    db_val = get_db_valuaciones()

    # 1. Cierres por mes (misma fuente swappable que valuacion_mensual).
    fechas_data = _cierres_fecha_data(id_cuenta, None, engine)
    cierres_buckets: dict[str, dict] = {}
    for f in fechas_data:
        fecha_str = str(f["_id"])
        bucket = fecha_str[:7]
        cierres_buckets[bucket] = {
            "_id":              bucket,
            "ultimo_dia":       fecha_str,
            "valuacion_cierre": f.get("valuacion") or 0,
            "n_posiciones":     f.get("n") or 0,
        }
    cierres = [cierres_buckets[k] for k in sorted(cierres_buckets.keys())]

    # 2. Flujos externos pesificados — guardamos el doc completo para
    # poder mostrarlo en la UI de debug.
    movimientos_raw = negocio_movimientos_rows(
        fields=("fecha", "categoria", "importe", "moneda",
                "op", "ticker", "comprobante", "informacion"),
        id_cuenta=str(id_cuenta),
        categorias=list(_FLUJOS_EXTERNOS_ALL),
    )

    mep_cache: dict[str, float | None] = {}
    flujos_by_mes: dict[str, dict[str, Any]] = {}
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
        bucket = flujos_by_mes.setdefault(
            mes, {"depositos": 0.0, "extracciones": 0.0, "items": [], "flujos_detalle": []}
        )
        if imp_ars != 0:
            bucket["items"].append((fecha, imp_ars))
        bucket["flujos_detalle"].append({
            "fecha":            fecha,
            "categoria":        m.get("categoria"),
            "op":               m.get("op"),
            "ticker":           m.get("ticker"),
            "comprobante":      m.get("comprobante"),
            "moneda":           moneda,
            "importe_original": round(imp_orig, 2),
            "mep_aplicado":     round(mep, 2) if mep is not None else None,
            "importe_ars":      round(imp_ars, 2),
            "informacion":      m.get("informacion"),
        })
        cat = m.get("categoria")
        if cat in _FLUJO_EXTERNO_DEPOSITO:
            bucket["depositos"] += imp_ars
        elif cat in _FLUJO_EXTERNO_EXTRACCION:
            bucket["extracciones"] += imp_ars

    # 3. Loop principal — armando el cashflow XIRR explícito por mes en ARS y USD.
    rows: list[dict[str, Any]] = []
    prev_val: float | None = None
    prev_val_usd: float | None = None
    prev_fecha: str | None = None
    twr_acum: float = 100.0
    twr_acum_usd: float = 100.0
    twr_iniciado = False
    mep_cierre_cache: dict[str, float | None] = {}

    for c in cierres:
        mes = c["_id"]
        f = flujos_by_mes.get(mes, {})
        depositos = float(f.get("depositos") or 0)
        extracciones = float(f.get("extracciones") or 0)
        # Suma signada directa (ver nota en valuacion_mensual) — sin
        # fallback por signo, que rompía con extracciones netas positivas.
        flujo_neto = depositos + extracciones
        cierre = float(c.get("valuacion_cierre") or 0)
        ultimo_dia = c.get("ultimo_dia")

        # ── MEP del cierre para dolarización ──
        mep_cierre: float | None = None
        if ultimo_dia and ultimo_dia not in mep_cierre_cache:
            mep_cierre_cache[ultimo_dia] = _get_mep_for_date(ultimo_dia, db_val)
        mep_cierre = mep_cierre_cache.get(ultimo_dia)

        # USD conversión de valores
        cierre_usd = cierre / mep_cierre if mep_cierre and mep_cierre > 0 else 0.0

        # USD conversión de flujos — usar MEP de cada fecha del flujo
        depositos_usd = 0.0
        extracciones_usd = 0.0
        for fecha_iso, imp_ars in (f.get("items") or []):
            if fecha_iso not in mep_cache:
                mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso, db_val)
            mep_flujo = mep_cache.get(fecha_iso)
            imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
            if imp_ars > 0:
                depositos_usd += imp_usd
            else:
                extracciones_usd += imp_usd
        flujo_neto_usd = depositos_usd + extracciones_usd

        delta_bruto = (cierre - prev_val) if prev_val is not None else None
        delta_real = (
            (delta_bruto - flujo_neto) if delta_bruto is not None else None
        )
        delta_bruto_usd = (cierre_usd - prev_val_usd) if prev_val_usd is not None else None
        delta_real_usd = (
            (delta_bruto_usd - flujo_neto_usd) if delta_bruto_usd is not None else None
        )

        # Armar cashflow XIRR explícito + calcular TEA (ARS).
        cashflow_xirr: list[dict[str, Any]] = []
        cashflow_xirr_usd: list[dict[str, Any]] = []
        tea_mensual: float | None = None
        tea_mensual_usd: float | None = None
        dias_periodo: int | None = None

        if (
            prev_val is not None and prev_val > 0
            and cierre > 0
            and prev_fecha and ultimo_dia
        ):
            try:
                d_inicio = _date.fromisoformat(prev_fecha)
                d_cierre = _date.fromisoformat(ultimo_dia)
                dias_periodo = (d_cierre - d_inicio).days
                dias_per = max(dias_periodo, 1)
                guess = (cierre / prev_val) ** (365.0 / dias_per) - 1
                guess = max(min(guess, 50.0), -0.99)

                # ── ARS XIRR ──
                cashflows: list[tuple[_date, float]] = [(d_inicio, +prev_val)]
                cashflow_xirr.append({
                    "fecha": prev_fecha, "monto": round(prev_val, 2),
                    "tipo": "valor_inicio",
                })
                for fecha_iso, imp_ars in (f.get("items") or []):
                    try:
                        cashflows.append((_date.fromisoformat(fecha_iso), float(imp_ars)))
                        cashflow_xirr.append({
                            "fecha": fecha_iso, "monto": round(float(imp_ars), 2),
                            "tipo": "flujo",
                        })
                    except (ValueError, TypeError):
                        continue
                cashflows.append((d_cierre, -cierre))
                cashflow_xirr.append({
                    "fecha": ultimo_dia, "monto": round(-cierre, 2),
                    "tipo": "valor_cierre",
                })
                tea_mensual = _xirr(cashflows, guess=guess)

                # ── USD XIRR ──
                if prev_val_usd is not None and prev_val_usd > 0 and cierre_usd > 0:
                    guess_usd = (cierre_usd / prev_val_usd) ** (365.0 / dias_per) - 1
                    guess_usd = max(min(guess_usd, 50.0), -0.99)
                    cashflows_usd: list[tuple[_date, float]] = [(d_inicio, +prev_val_usd)]
                    cashflow_xirr_usd.append({
                        "fecha": prev_fecha, "monto": round(prev_val_usd, 2),
                        "tipo": "valor_inicio",
                    })
                    for fecha_iso, imp_ars in (f.get("items") or []):
                        try:
                            if fecha_iso not in mep_cache:
                                mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso, db_val)
                            mep_flujo = mep_cache.get(fecha_iso)
                            imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
                            if imp_usd != 0:
                                cashflows_usd.append((_date.fromisoformat(fecha_iso), float(imp_usd)))
                                cashflow_xirr_usd.append({
                                    "fecha": fecha_iso, "monto": round(float(imp_usd), 2),
                                    "tipo": "flujo",
                                })
                        except (ValueError, TypeError):
                            continue
                    cashflows_usd.append((d_cierre, -cierre_usd))
                    cashflow_xirr_usd.append({
                        "fecha": ultimo_dia, "monto": round(-cierre_usd, 2),
                        "tipo": "valor_cierre",
                    })
                    tea_mensual_usd = _xirr(cashflows_usd, guess=guess_usd)
            except ValueError:
                tea_mensual = None
                tea_mensual_usd = None

        # TEM des-anualizada al período exacto entre los dos cierres.
        tem_periodo: float | None = None
        tem_periodo_usd: float | None = None
        if tea_mensual is not None and dias_periodo and dias_periodo > 0:
            tem_periodo = (1 + tea_mensual) ** (dias_periodo / 365) - 1
        if tea_mensual_usd is not None and dias_periodo and dias_periodo > 0:
            tem_periodo_usd = (1 + tea_mensual_usd) ** (dias_periodo / 365) - 1

        if not twr_iniciado:
            twr_acum = 100.0
            twr_acum_usd = 100.0
            twr_iniciado = True
        elif tem_periodo is not None:
            twr_acum = twr_acum * (1 + tem_periodo)
        if tem_periodo_usd is not None:
            twr_acum_usd = twr_acum_usd * (1 + tem_periodo_usd)

        rows.append({
            "mes":                  mes,
            "fecha_inicio":         prev_fecha,
            "fecha_cierre":         ultimo_dia,
            "dias_periodo":         dias_periodo,
            "valor_inicio":         round(prev_val, 2) if prev_val is not None else None,
            "valor_cierre":         round(cierre, 2),
            "depositos":            round(depositos, 2),
            "extracciones":         round(extracciones, 2),
            "flujo_neto":           round(flujo_neto, 2),
            "delta_bruto":          round(delta_bruto, 2) if delta_bruto is not None else None,
            "delta_real":           round(delta_real, 2) if delta_real is not None else None,
            "flujos_individuales":  f.get("flujos_detalle") or [],
            "cashflow_xirr":        cashflow_xirr,
            "tea_mensual":          round(tea_mensual, 6) if tea_mensual is not None else None,
            "tem_periodo":          round(tem_periodo, 6) if tem_periodo is not None else None,
            "twr_base100_acum":     round(twr_acum, 4),
            # USD parallels
            "mep_cierre":           round(mep_cierre, 4) if mep_cierre is not None else None,
            "valor_inicio_usd":     round(prev_val_usd, 2) if prev_val_usd is not None else None,
            "valor_cierre_usd":     round(cierre_usd, 2),
            "depositos_usd":        round(depositos_usd, 2),
            "extracciones_usd":     round(extracciones_usd, 2),
            "flujo_neto_usd":       round(flujo_neto_usd, 2),
            "delta_bruto_usd":      round(delta_bruto_usd, 2) if delta_bruto_usd is not None else None,
            "delta_real_usd":       round(delta_real_usd, 2) if delta_real_usd is not None else None,
            "cashflow_xirr_usd":    cashflow_xirr_usd,
            "tea_mensual_usd":      round(tea_mensual_usd, 6) if tea_mensual_usd is not None else None,
            "tem_periodo_usd":      round(tem_periodo_usd, 6) if tem_periodo_usd is not None else None,
            "twr_base100_acum_usd": round(twr_acum_usd, 4),
            "n_posiciones":         c.get("n_posiciones", 0),
        })
        prev_val = cierre
        prev_val_usd = cierre_usd
        prev_fecha = ultimo_dia

    # Mes más reciente primero — coherente con valuacion_mensual.
    rows.reverse()

    resumen: dict[str, Any] = {
        "primer_mes":       rows[-1]["mes"] if rows else None,
        "ultimo_mes":       rows[0]["mes"]  if rows else None,
        "n_meses":          len(rows),
        "twr_final":        rows[0]["twr_base100_acum"] if rows else None,
        "twr_final_usd":    rows[0]["twr_base100_acum_usd"] if rows else None,
        "ganancia_pct":     (
            round(rows[0]["twr_base100_acum"] - 100, 2)
            if rows and rows[0].get("twr_base100_acum") is not None
            else None
        ),
        "ganancia_pct_usd": (
            round(rows[0]["twr_base100_acum_usd"] - 100, 2)
            if rows and rows[0].get("twr_base100_acum_usd") is not None
            else None
        ),
    }

    return {
        "id_cuenta": id_cuenta,
        "meses":     rows,
        "n_meses":   len(rows),
        "resumen":   resumen,
    }


@cached(ttl=60)
def posiciones_actuales(
    id_cuenta: str, fecha: str | None = None, cartera: str | None = None,
    asof: bool = False,
) -> dict[str, Any]:
    """Posiciones de un fecha_snapshot dado para la cuenta.

    Read directo de Valuaciones.AuM (sin cost basis ni boletos): si
    `fecha` es None, usa el snapshot más reciente. Si se pasa una
    fecha (YYYY-MM-DD), usa exactamente esa. Devuelve la lista de
    unidades con cantidad, precio, valuación y share del total.

    `asof=True`: si la fecha pedida NO tiene snapshot (los de AuM son
    irregulares — fin de mes + diarios recientes), resuelve al snapshot
    disponible más cercano <= fecha (la posición que se tenía a ese día).
    NO es "la latest": es el cierre anterior real. Lo usa el buscador por
    fecha de Carteras para auditar cualquier día sin caer en vacío. El
    dict devuelto trae la fecha REAL usada (la UI la muestra).

    Sirve como "snapshot" en el panel derecho de /valuaciones, con
    selector de fecha para ver posiciones históricas (clickear una
    fila de la tabla mensual cambia la fecha mostrada).
    """
    db_val = get_db_valuaciones()

    if fecha:
        # Validar que efectivamente exista esa fecha para la cuenta.
        exists = db_val["AuM"].count_documents(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha},
            limit=1,
        )
        if not exists:
            prev = list(
                db_val["AuM"]
                .find({"id_cuenta": id_cuenta, "fecha_snapshot": {"$lte": fecha}},
                      {"_id": 0, "fecha_snapshot": 1})
                .sort("fecha_snapshot", -1)
                .limit(1)
            ) if asof else []
            if not prev:
                # Sin asof (o sin cierre anterior): vacío en vez de mentir.
                return {
                    "id_cuenta": id_cuenta, "fecha": fecha,
                    "posiciones": [], "total": 0.0, "n": 0,
                }
            fecha = prev[0]["fecha_snapshot"]   # asof: el más cercano <= fecha
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
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha,
             **({"CARTERA": cartera} if cartera else {})},
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

    # Enriquecer con master data desde Valuaciones.Assets (UPPERCASE) — la
    # fuente de verdad que edita Manager → Assets y que jobs/aum.py sincroniza
    # con cada unidad del snapshot. Trae ticker/emisor/calificación/vencimiento
    # + cartera/clase en una sola query. Antes esto joinaba contra
    # TitulosAPI.AssetsAPI (copia derivada) y los bonos sin match ahí caían al
    # `unidad` crudo largo ("[9396] AO28 - BONO TESORO NAC...").
    unidades = set(by_unidad.keys())
    enrich_by_unidad: dict[str, dict] = {}
    if unidades:
        for a in assets_rows(["CARTERA", "CLASE_ACTIVO", "TICKER", "EMISOR",
                              "CALIFICACION", "VENCIMIENTO"]):
            u = a["unidad"]
            if u in unidades:
                enrich_by_unidad[u] = {
                    "cartera":      a["CARTERA"] or "OTROS",
                    "clase_activo": a["CLASE_ACTIVO"],
                    "ticker":       a["TICKER"],
                    "emisor":       a["EMISOR"],
                    "calificacion": a["CALIFICACION"],
                    "vencimiento":  a["VENCIMIENTO"],
                }

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
                "unidad":       r["ticker"],
                "ticker":       enrich_by_unidad.get(r["ticker"], {}).get("ticker") or r["ticker"],
                "emisor":       enrich_by_unidad.get(r["ticker"], {}).get("emisor") or "-",
                "clase_activo": enrich_by_unidad.get(r["ticker"], {}).get("clase_activo") or "-",
                "cartera":      enrich_by_unidad.get(r["ticker"], {}).get("cartera") or "",
                "calificacion": enrich_by_unidad.get(r["ticker"], {}).get("calificacion") or "-",
                "vencimiento":  str(enrich_by_unidad.get(r["ticker"], {}).get("vencimiento") or "")[:10] or None,
                "tipo":         str(r["tipo"]) if r["tipo"] not in (None, "") else None,
                "cantidad":     round(r["cantidad"], 4),
                "precio":       round(r["precio"], 4),
                "valuacion":    round(r["valuacion"], 2),
                "share":        (
                    round((r["valuacion"] / total) * 100, 2)
                    if total else None
                ),
            }
            for r in rows
        ],
        "total": round(total, 2),
        "n":     len(rows),
    }


def aum_raw(id_cuenta: str, fecha: str | None = None) -> dict[str, Any]:
    """Docs CRUDOS de Valuaciones.AuM para una (cuenta, fecha_snapshot).

    Sin agrupar ni enriquecer — devuelve cada doc tal cual está en Mongo
    (unidad, cantidad, precio, valuacion, tipoTitulo). Pensado para
    validar a ojo si los precios y las valuaciones están bien:
    `valuacion` deberí­a ser `cantidad × precio` (con el divisor /100 que
    aplique según tipo — ese cálculo lo hace jobs/aum.py al persistir).

    No cacheado — siempre muestra el estado actual de la colección.

    Args:
        id_cuenta: id numérico, ej "805".
        fecha: fecha_snapshot YYYY-MM-DD. Si None o inexistente, usa el
            último snapshot disponible para la cuenta.

    Returns:
        {
          id_cuenta, fecha, fechas_disponibles: [...],
          posiciones: [{unidad, cuenta, tipo, moneda, cantidad, precio,
                        valuacion, valuacion_esperada, desvio}, ...],
          total, n,
        }
    """
    db_val = get_db_valuaciones()

    fechas = sorted(
        str(f) for f in db_val["AuM"].distinct("fecha_snapshot", {"id_cuenta": id_cuenta})
    )
    if not fechas:
        return {
            "id_cuenta": id_cuenta, "fecha": None, "fechas_disponibles": [],
            "posiciones": [], "total": 0.0, "n": 0,
        }
    if not fecha or fecha not in fechas:
        fecha = fechas[-1]

    docs = list(
        db_val["AuM"]
        .find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha},
            {"_id": 0, "unidad": 1, "cuenta": 1, "cantidad": 1, "precio": 1,
             "valuacion": 1, "tipoTitulo": 1, "moneda": 1},
        )
        .sort("valuacion", -1)
    )

    posiciones: list[dict[str, Any]] = []
    total = 0.0
    for d in docs:
        try:
            qty = float(d.get("cantidad") or 0)
            precio = float(d.get("precio") or 0)
            val = float(d.get("valuacion") or 0)
        except (TypeError, ValueError):
            qty = precio = val = 0.0
        # valuacion_esperada = cantidad × precio crudo (sin el /100 por tipo).
        # `desvio` ayuda a detectar precios mal traídos: si valuacion no es
        # ni qty×precio ni qty×precio/100, algo está roto en el dato.
        esperada = qty * precio
        total += val
        posiciones.append({
            "unidad":             d.get("unidad"),
            "cuenta":             d.get("cuenta"),
            "tipo":               d.get("tipoTitulo"),
            "moneda":             d.get("moneda"),
            "cantidad":           round(qty, 6),
            "precio":             round(precio, 6),
            "valuacion":          round(val, 2),
            "valuacion_esperada": round(esperada, 2),
            "desvio":             round(val - esperada, 2),
        })

    return {
        "id_cuenta":          id_cuenta,
        "fecha":              fecha,
        "fechas_disponibles": fechas,
        "posiciones":         posiciones,
        "total":              round(total, 2),
        "n":                  len(posiciones),
    }


# Unidades / tipos que tratamos como efectivo → van al cajón "OTROS" de
# la descomposición de variación (no son "un título que se movió").
_CASH_UNIDADES = {"ARS", "USD", "USDC", "USDL"}


def _es_cash(unidad: str | None, tipo: str | None) -> bool:
    u = (unidad or "").strip().upper()
    if u in _CASH_UNIDADES:
        return True
    if (tipo or "").strip().lower() == "moneda":
        return True
    return "DEPOSITO" in u or "DEPÓSITO" in u


def variacion_titulos(id_cuenta: str, fecha: str) -> dict[str, Any]:
    """Descompone la variación del portfolio entre `fecha` y el snapshot
    anterior, por título — separando efecto MERCADO vs efecto OPERADO.

    Por cada unidad con valuación en alguno de los dos snapshots:
        precio_efectivo = valuacion / cantidad   (ya incluye /100 o +1)
        delta_mercado = (pe_actual − pe_previo) × cantidad_previa
        delta_operado = (cantidad_actual − cantidad_previa) × pe_actual
        delta_total   = valuacion_actual − valuacion_previa
                      = delta_mercado + delta_operado   (cierra exacto)

    Unidad nueva → todo a `operado` (la compraste). Cerrada → todo a
    `operado` negativo (la vendiste). El efectivo (ARS/USD/…) se agrega
    en `otros` — no es "un título que rindió".

    No cacheado — para validar siempre muestra el estado actual.

    Returns:
        {id_cuenta, fecha, fecha_anterior,
         filas: [{unidad, tipo, val_anterior, val_actual, delta_mercado,
                  delta_operado, delta_total, estado}, ...],
         otros: {delta_mercado, delta_operado, delta_total, val_anterior,
                 val_actual, n},
         totales: {val_anterior, val_actual, delta_mercado, delta_operado,
                   delta_total}}
    """
    db_val = get_db_valuaciones()
    fechas = sorted(
        str(f) for f in db_val["AuM"].distinct("fecha_snapshot", {"id_cuenta": id_cuenta})
    )
    base = {"id_cuenta": id_cuenta, "fecha": fecha, "fecha_anterior": None,
            "filas": [], "otros": None, "totales": None}
    if fecha not in fechas:
        return {**base, "error": "fecha sin snapshot para la cuenta"}

    # Comparamos contra el CIERRE DEL MES ANTERIOR — no contra el snapshot
    # anterior cronológico. Desde marzo 2026 hay snapshots diarios, así que
    # el snapshot previo sería el día anterior y la "variación mensual"
    # quedaría mal. El cierre de cada mes calendario = último snapshot del
    # mes (mismo criterio que valuacion_mensual).
    cierres: dict[str, str] = {}
    for f in fechas:  # asc → el último snapshot del mes gana
        cierres[f[:7]] = f
    cierres_ord = [cierres[m] for m in sorted(cierres)]
    if fecha in cierres_ord:
        idx = cierres_ord.index(fecha)
        if idx == 0:
            return {**base, "error": "no hay mes anterior — es el primer mes"}
        fecha_prev = cierres_ord[idx - 1]
    else:
        # fecha no es un cierre de mes (caso raro) → snapshot anterior directo.
        idx = fechas.index(fecha)
        if idx == 0:
            return {**base, "error": "no hay snapshot anterior"}
        fecha_prev = fechas[idx - 1]

    def _cargar(f: str) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for d in db_val["AuM"].find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": f},
            {"_id": 0, "unidad": 1, "tipoTitulo": 1, "cantidad": 1, "valuacion": 1},
        ):
            u = d.get("unidad")
            if not u:
                continue
            try:
                cant = float(d.get("cantidad") or 0)
                val = float(d.get("valuacion") or 0)
            except (TypeError, ValueError):
                continue
            e = agg.setdefault(u, {"tipo": d.get("tipoTitulo"),
                                   "cantidad": 0.0, "valuacion": 0.0})
            e["cantidad"] += cant
            e["valuacion"] += val
        return agg

    prev = _cargar(fecha_prev)
    act = _cargar(fecha)

    filas: list[dict] = []
    otros = {"delta_mercado": 0.0, "delta_operado": 0.0, "delta_total": 0.0,
             "val_anterior": 0.0, "val_actual": 0.0, "n": 0}

    for u in set(prev) | set(act):
        p = prev.get(u)
        a = act.get(u)
        cant_prev = p["cantidad"] if p else 0.0
        cant_act = a["cantidad"] if a else 0.0
        val_prev = p["valuacion"] if p else 0.0
        val_act = a["valuacion"] if a else 0.0
        tipo = (a or p)["tipo"]
        delta_total = val_act - val_prev
        if cant_prev != 0 and cant_act != 0:
            pe_prev = val_prev / cant_prev
            pe_act = val_act / cant_act
            delta_mercado = (pe_act - pe_prev) * cant_prev
            delta_operado = (cant_act - cant_prev) * pe_act
        else:
            # Unidad nueva o cerrada → todo el cambio es operatoria.
            delta_mercado = 0.0
            delta_operado = delta_total

        if _es_cash(u, tipo):
            otros["delta_mercado"] += delta_mercado
            otros["delta_operado"] += delta_operado
            otros["delta_total"] += delta_total
            otros["val_anterior"] += val_prev
            otros["val_actual"] += val_act
            otros["n"] += 1
        else:
            estado = "ambos" if (p and a) else ("nuevo" if a else "cerrado")
            filas.append({
                "unidad":        u,
                "tipo":          tipo,
                "val_anterior":  round(val_prev, 2),
                "val_actual":    round(val_act, 2),
                "delta_mercado": round(delta_mercado, 2),
                "delta_operado": round(delta_operado, 2),
                "delta_total":   round(delta_total, 2),
                "estado":        estado,
            })

    filas.sort(key=lambda r: -abs(r["delta_total"]))

    tot_merc = sum(r["delta_mercado"] for r in filas) + otros["delta_mercado"]
    tot_oper = sum(r["delta_operado"] for r in filas) + otros["delta_operado"]
    tot_delta = sum(r["delta_total"] for r in filas) + otros["delta_total"]
    tot_prev = sum(r["val_anterior"] for r in filas) + otros["val_anterior"]
    tot_act = sum(r["val_actual"] for r in filas) + otros["val_actual"]

    return {
        "id_cuenta":      id_cuenta,
        "fecha":          fecha,
        "fecha_anterior": fecha_prev,
        "filas":          filas,
        "otros":          {
            k: (round(v, 2) if isinstance(v, float) else v)
            for k, v in otros.items()
        },
        "totales": {
            "val_anterior":  round(tot_prev, 2),
            "val_actual":    round(tot_act, 2),
            "delta_mercado": round(tot_merc, 2),
            "delta_operado": round(tot_oper, 2),
            "delta_total":   round(tot_delta, 2),
        },
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
    db_val = get_db_valuaciones()
    mes = fecha_anchor[:7]  # YYYY-MM

    # Match: cuenta por prefijo numérico, fecha en el mes target,
    # categoria entre los flujos externos.
    docs = negocio_movimientos_rows(
        fields=("fecha", "comprobante", "categoria", "importe", "moneda",
                "op", "ticker", "informacion", "cuenta"),
        cuenta_prefix=str(id_cuenta),
        categorias=list(_FLUJOS_EXTERNOS_ALL),
        fecha_prefix=mes,
        order=True,
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


def construir_consolidado() -> list[dict[str, Any]]:
    """Calcula la fila consolidada de CADA cuenta — operación pesada.

    Recorre todas las cuentas del último snapshot de AuM llamando
    `valuacion_mensual` (sin modificarla): toma el mes más reciente
    (valor de cierre, twr_base100, TEM, TEA) y el PnL acumulado
    (Σ delta_real de todos los meses), en ARS y USD.

    Lo corre el cron `jobs.consolidado_cuentas`, NO el endpoint — iterar
    N cuentas en vivo se pasa del timeout HTTP (502). El job persiste el
    resultado en `Valuaciones.ConsolidadoCuentas`.
    """
    # SQL: Valuaciones.AuM (Mongo) fue eliminada → la lista de cuentas sale de
    # portafolio.tenencia (portfolio_sql), no del portfolio.py Mongo (que leía AuM
    # y devolvía [] → construir_consolidado daba 0 filas).
    from api.services.portfolio_sql import listar_cuentas

    rows: list[dict[str, Any]] = []
    for c in listar_cuentas():
        id_cta = c.get("id_cuenta")
        if id_cta is None:
            continue
        try:
            # engine="sql": los cierres salen de portafolio.tenencia (Valuaciones.AuM
            # fue eliminado en la migración SQL). Los flujos ya salen de SQL.
            m = valuacion_mensual(id_cuenta=str(id_cta), engine="sql")
        except Exception:
            continue
        meses = m.get("meses") or []
        if not meses:
            continue
        ult = meses[0]   # `meses` viene descendente → [0] es el más reciente
        pnl_ars = sum(float(x.get("delta_real") or 0) for x in meses)
        pnl_usd = sum(float(x.get("delta_real_usd") or 0) for x in meses)
        rows.append({
            "cuenta":       c.get("cuenta") or "",
            "id_cuenta":    id_cta,
            "ultimo_dia":   ult.get("ultimo_dia"),
            "valor_ars":    round(float(ult.get("valuacion_cierre") or 0), 2),
            "valor_usd":    round(float(ult.get("valuacion_cierre_usd") or 0), 2),
            "base100_ars":  ult.get("twr_base100"),
            "base100_usd":  ult.get("twr_base100_usd"),
            "pnl_acum_ars": round(pnl_ars, 2),
            "pnl_acum_usd": round(pnl_usd, 2),
            "tem_ars":      ult.get("tem_periodo"),
            "tem_usd":      ult.get("tem_periodo_usd"),
            "tea_ars":      ult.get("tea_mensual"),
            "tea_usd":      ult.get("tea_mensual_usd"),
        })
    return rows


@cached(ttl=300)
def valuacion_consolidada(
    filtro_cuenta: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Una fila por cuenta: valor, base 100, PnL acum (ARS y USD).

    LECTURA LIVIANA: lee `Valuaciones.ConsolidadoCuentas`, precalculada
    offline por el cron `jobs.consolidado_cuentas` (el cálculo recorre N
    cuentas → no entra en el timeout HTTP). Sólo aplica el filtro de tipo
    de cuenta sobre lo ya calculado.

    `filtro_cuenta`: "todas" | "accionistas" | "sin_accionistas" |
    "cooperativas" | "productores" (ver `_cuentas_filter`).

    `scope` restringe a las cuentas del grupo del usuario (None = sin
    restricción: admin o usuario sin grupo).

    Si la colección está vacía → `rows: []` (falta correr el job una vez).
    """
    db_val = get_db_valuaciones()
    docs = list(db_val["ConsolidadoCuentas"].find(
        {}, {"_id": 0, "computed_at": 0},
    ))

    # Scoping de grupos — subset de cuentas visibles para el usuario.
    if scope is not None:
        permitidas = set(scope)
        docs = [d for d in docs if str(d.get("id_cuenta", "")) in permitidas]

    # Filtro por tipo de cuenta — se aplica EN PYTHON sobre los docs ya cargados
    # (cada uno trae `cuenta`="[N] NOMBRE" + `id_cuenta`). Antes cruzaba contra
    # Valuaciones.AuM (eliminada en la migración SQL) → ahora membership directa.
    if filtro_cuenta and filtro_cuenta != "todas":
        from api.services._cuentas_filter import (
            _cuentas_accionistas,
            _ids_cuenta_productores,
        )
        accs = set(_cuentas_accionistas())          # strings "[N] NOMBRE"
        prods = set(_ids_cuenta_productores())      # id_cuenta

        def _ok(d: dict) -> bool:
            cuenta = d.get("cuenta") or ""
            idc = str(d.get("id_cuenta") or "")
            if filtro_cuenta == "accionistas":
                return cuenta in accs
            if filtro_cuenta == "sin_accionistas":
                return cuenta not in accs
            if filtro_cuenta == "cooperativas":
                return cuenta not in accs and "coop" in cuenta.lower()
            if filtro_cuenta == "productores":
                return idc in prods
            return True

        docs = [d for d in docs if _ok(d)]

    return {"rows": docs, "n": len(docs), "filtro_cuenta": filtro_cuenta}
