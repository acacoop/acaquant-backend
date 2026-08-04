"""Valuaciones — performance e historia por cuenta. SQL-only (decomiso Mongo).

Dos enfoques convivientes:

(A) AUM-BASED [usado por /serie y /mensual]
    Cierres por fecha desde SQL `portafolio.tenencia` (vía valuaciones_sql /
    _cierres_fecha_data) — el snapshot diario MTM canónico (normalizado por
    cartera al persistir). Para cada (id_cuenta, fecha) sumamos `valuacion` de
    todas las posiciones. Combinado con flujos externos (depósitos/extracciones
    de operaciones.negocio_movimientos) da la mensualización "valor de cierre +
    flujo neto" que pide la vista.

(B) COST-BASIS LEDGER [usado por /posiciones]
    Reconstruye lots de boletos compra/venta (operaciones.negocio_movimientos)
    con weighted-average cost. PnL realizado vs no realizado por ticker.

Los gemelos de solo-lectura serie_valor_cuenta / posiciones_actuales /
variacion_titulos / valuacion_consolidada viven SQL-native en `valuaciones_sql`
(el router los usa directo). Acá quedan: mensual (+debug), movimientos_mes,
posiciones_cuenta, aum_raw, construir_consolidado y el helper puro `_es_cash`.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any
from zoneinfo import ZoneInfo

from api.cache import cached
from api.services._negocio_sql_read import negocio_movimientos_rows
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


# ── Serie MEP precargada ──────────────────────────────────────────────────
# `valuaciones.dolar` es append-only y el último MEP de un día YA CERRADO no se
# mueve más → la serie diaria se precarga en UNA query y se cachea en proceso
# (TTL corto, para tomar un backfill eventual). Antes cada fecha distinta era un
# SELECT: dentro de los loops de movimientos, por cuenta, y `construir_consolidado`
# lo repetía para TODAS las cuentas.
# HOY (o una fecha futura) NO sale de la serie: el MEP del día sigue moviéndose
# durante la rueda y tiene que leerse vivo de la DB, como hasta ahora.
_MEP_SERIE_TTL_S = 300.0
_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_mep_serie: list[tuple[str, float]] = []
_mep_dias: list[str] = []
_mep_serie_ts: float = 0.0
_mep_lock = threading.Lock()


def _mep_serie_cerrada() -> tuple[list[tuple[str, float]], list[str]]:
    """Serie [(día ART, último MEP > 0)] + sus días, cacheada con TTL."""
    global _mep_serie, _mep_dias, _mep_serie_ts
    with _mep_lock:
        if not _mep_dias or (time.monotonic() - _mep_serie_ts) > _MEP_SERIE_TTL_S:
            from core import dolar_sql
            serie = dolar_sql.mep_serie_dias()
            if serie:   # serie vacía = DB caída → no se cachea, se reintenta
                _mep_serie = serie
                _mep_dias = [d for d, _ in serie]
                _mep_serie_ts = time.monotonic()
        return _mep_serie, _mep_dias


def _get_mep_for_date(fecha_iso: str) -> float | None:
    """MEP histórico — misma semántica que la implementación ÚNICA
    (_mep.get_mep_for_date: último MEP > 0 con timestamp <= fin-de-día ART,
    arrastrando el último día CON DATO si la fecha no tiene tick).

    Los días ya cerrados se resuelven contra la serie precargada; hoy / futuro
    van a la DB (valor vivo). Cualquier `fecha_iso` que no sea 'YYYY-MM-DD' cae
    también a la DB — la serie compara fechas como string."""
    from core import dolar_sql
    if dolar_sql.es_fecha_dia(fecha_iso) and fecha_iso < _datetime.now(_ART).date().isoformat():
        serie, dias = _mep_serie_cerrada()
        return dolar_sql.mep_en_serie(serie, dias, fecha_iso)
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

    # 3. Map short → long ticker via mercado.curvas (ticker_corto → ticker), SQL.
    short_tickers = set(state.keys())
    from core import curvas_sql
    curvas = [c for c in curvas_sql.cargar_todos()
              if c.get("ticker_corto") in short_tickers]
    short_to_curva: dict[str, dict] = {
        c["ticker_corto"]: c for c in curvas if c.get("ticker_corto")
    }

    # 4. Prices live de MarketSnapshot — SQL-only (mercado.market_snapshot, key=ticker).
    long_tickers = [c["ticker"] for c in short_to_curva.values() if c.get("ticker")]
    snapshots: dict[str, dict] = {}
    if long_tickers:
        from core.market_snapshot import snapshot_docs
        snapshots = snapshot_docs(long_tickers)

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


def _cierres_fecha_data(id_cuenta: str, cartera: str | None = None) -> list[dict]:
    """Cierres por fecha_snapshot → [{_id, valuacion, n}] ordenado asc, desde SQL
    `portafolio.tenencia` (vía valuaciones_sql). El resto del cálculo mensual
    (flujos/MEP/XIRR/TWR) opera sobre esta lista sin cambios."""
    from api.services import valuaciones_sql as _vsql
    return _vsql.cierres_fecha_data(id_cuenta, cartera)


@cached(ttl=300)
def valuacion_mensual(id_cuenta: str) -> dict[str, Any]:
    """Versión CACHEADA (flujo externo: depósitos/extracciones) — la usa Carteras.
    La variante con flujo custom (NEGOCIO→Valuaciones) es `_valuacion_mensual`."""
    return _valuacion_mensual(id_cuenta=id_cuenta)


def _calcular_meses(
    id_cuenta: str,
    *,
    flujos_override: dict | None = None,
    cartera: str | None = None,
    detalle: bool = False,
) -> list[dict[str, Any]]:
    """CORE ÚNICO del cálculo mensual (cierres, flujos pesificados, XIRR ARS/USD,
    TEM, TWR base 100). Lo comparten `_valuacion_mensual` (producción) y
    `valuacion_mensual_debug` (auditoría) — antes eran ~600 líneas gemelas y cada
    fix al cálculo financiero había que aplicarlo dos veces (ya había divergido).

    `detalle=True` agrega a cada mes `flujos_detalle` (docs completos de los
    movimientos) y `cashflow_xirr` / `cashflow_xirr_usd` (la lista exacta de
    (fecha, monto) que recibe xirr(), reproducible en Excel con TIR.NO.PER).

    Devuelve la lista de meses ASC con valores SIN redondear — los wrappers
    mapean al shape público de cada endpoint.

    Bucket de cierre vs mes calendario: el snapshot del día 1 de un mes
    representa la valuación al INICIO del mes corriente == cierre del MES
    ANTERIOR → se reasigna al bucket del mes anterior. Sorted asc → el último
    snapshot del bucket gana (para meses con daily, el último día hábil).
    """
    # 1. Valuación al cierre de cada mes (último fecha_snapshot del mes).
    fechas_data = _cierres_fecha_data(id_cuenta, cartera)
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
    # Se hace en Python porque la tasa MEP es per-fecha del MOVIMIENTO, no por
    # mes. `flujos_override`: un caller puede pasar OTRO flujo (mismo shape
    # {mes: {depositos, extracciones, items:[(fecha_iso, imp_ars_signado)]}}) y
    # se usa tal cual — ej. NEGOCIO→Valuaciones usa el neto de los boletos.
    mep_cache: dict[str, float | None] = {}
    if flujos_override is not None:
        flujos_by_mes = flujos_override
        movimientos_raw: list[dict] = []
    else:
        fields = ("fecha", "categoria", "importe", "moneda")
        if detalle:
            fields += ("op", "ticker", "comprobante", "informacion")
        movimientos_raw = negocio_movimientos_rows(
            fields=fields,
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
            mep_cache[fecha] = _get_mep_for_date(fecha)
        mep = mep_cache.get(fecha)
        imp_ars = _pesificar(imp_orig, moneda, mep)
        mes = fecha[:7]
        bucket = flujos_by_mes.setdefault(
            mes, {"depositos": 0.0, "extracciones": 0.0, "items": [], "flujos_detalle": []}
        )
        # Importes con signo cliente correcto (+ depósito, - extracción)
        # ya vienen de aunesa_negocio.py. Ignoramos importes nulos para no
        # ensuciar XIRR con flujos = 0 (no aportan info y multiplican iter).
        if imp_ars != 0:
            bucket["items"].append((fecha, imp_ars))
        if detalle:
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

    # 3. Merge y compute (deltas, XIRR, TEM, TWR) — ARS y USD paralelos.
    # delta_bruto = cierre_t - cierre_{t-1}; delta_real = delta_bruto - flujo_neto.
    # TEA del mes vía XIRR (TIR.NO.PER de Excel) sobre el cashflow
    # [(inicio, +V_prev), (fecha_flujo, ±imp)..., (cierre, -V_cierre)].
    # TEM = (1 + tea)^(días/365) − 1; twr_base100 = 100 × Π(1 + TEM_t).
    rows: list[dict[str, Any]] = []
    prev_val: float | None = None
    prev_fecha: str | None = None
    prev_val_usd: float | None = None
    twr_acum: float = 100.0
    twr_acum_usd: float = 100.0
    twr_iniciado = False
    mep_cierre_cache: dict[str, float | None] = {}

    for c in cierres:
        mes = c["_id"]
        f = flujos_by_mes.get(mes, {})
        depositos = float(f.get("depositos") or 0)
        extracciones = float(f.get("extracciones") or 0)
        # Sumas SIGNADAS: el neto es la suma directa. NO hay fallback por signo
        # (rompía cuando una serie tenía extracciones que netaban positivo —
        # reversas/refunds — y contaminaba delta_real, XIRR y TWR).
        flujo_neto = depositos + extracciones
        cierre = float(c.get("valuacion_cierre") or 0)
        ultimo_dia = c.get("ultimo_dia")

        # ── MEP del cierre para dolarización ──
        if ultimo_dia and ultimo_dia not in mep_cierre_cache:
            mep_cierre_cache[ultimo_dia] = _get_mep_for_date(ultimo_dia)
        mep_cierre = mep_cierre_cache.get(ultimo_dia)
        cierre_usd = cierre / mep_cierre if mep_cierre and mep_cierre > 0 else 0.0

        # USD conversión de flujos — MEP de cada fecha del flujo.
        depositos_usd = 0.0
        extracciones_usd = 0.0
        for fecha_iso, imp_ars in (f.get("items") or []):
            if fecha_iso not in mep_cache:
                mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso)
            mep_flujo = mep_cache.get(fecha_iso)
            imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
            if imp_ars > 0:
                depositos_usd += imp_usd
            else:
                extracciones_usd += imp_usd
        flujo_neto_usd = depositos_usd + extracciones_usd

        delta_bruto = (cierre - prev_val) if prev_val is not None else None
        delta_real = (delta_bruto - flujo_neto) if delta_bruto is not None else None
        delta_bruto_usd = (cierre_usd - prev_val_usd) if prev_val_usd is not None else None
        delta_real_usd = (
            (delta_bruto_usd - flujo_neto_usd) if delta_bruto_usd is not None else None
        )

        dias_periodo: int | None = None
        if prev_fecha and ultimo_dia:
            try:
                dias_periodo = (
                    _date.fromisoformat(ultimo_dia) - _date.fromisoformat(prev_fecha)
                ).days
            except ValueError:
                dias_periodo = None

        # ── TEA del mes vía XIRR (ARS) — try/except propio: un fallo acá no
        # anula el cálculo USD (y viceversa) ──
        cashflow_xirr: list[dict[str, Any]] = []
        tea_mensual: float | None = None
        r_mes: float | None = None
        if (
            prev_val is not None and prev_val > 0
            and cierre > 0
            and prev_fecha and ultimo_dia and dias_periodo is not None
        ):
            try:
                d_inicio = _date.fromisoformat(prev_fecha)
                d_cierre = _date.fromisoformat(ultimo_dia)
                dias_per = max(dias_periodo, 1)
                guess = (cierre / prev_val) ** (365.0 / dias_per) - 1
                guess = max(min(guess, 50.0), -0.99)
                cashflows: list[tuple[_date, float]] = [(d_inicio, +prev_val)]
                if detalle:
                    cashflow_xirr.append({
                        "fecha": prev_fecha, "monto": round(prev_val, 2),
                        "tipo": "valor_inicio",
                    })
                for fecha_iso, imp_ars in (f.get("items") or []):
                    try:
                        cashflows.append((_date.fromisoformat(fecha_iso), float(imp_ars)))
                        if detalle:
                            cashflow_xirr.append({
                                "fecha": fecha_iso, "monto": round(float(imp_ars), 2),
                                "tipo": "flujo",
                            })
                    except (ValueError, TypeError):
                        continue
                cashflows.append((d_cierre, -cierre))
                if detalle:
                    cashflow_xirr.append({
                        "fecha": ultimo_dia, "monto": round(-cierre, 2),
                        "tipo": "valor_cierre",
                    })
                tea_mensual = _xirr(cashflows, guess=guess)
                if tea_mensual is not None and dias_periodo > 0:
                    r_mes = (1 + tea_mensual) ** (dias_periodo / 365) - 1
            except ValueError:
                tea_mensual = None

        # ── TEA del mes vía XIRR (USD) ──
        cashflow_xirr_usd: list[dict[str, Any]] = []
        tea_mensual_usd: float | None = None
        r_mes_usd: float | None = None
        if (
            prev_val_usd is not None and prev_val_usd > 0
            and cierre_usd > 0
            and prev_fecha and ultimo_dia and dias_periodo is not None
        ):
            try:
                d_inicio = _date.fromisoformat(prev_fecha)
                d_cierre = _date.fromisoformat(ultimo_dia)
                dias_per = max(dias_periodo, 1)
                guess_usd = (cierre_usd / prev_val_usd) ** (365.0 / dias_per) - 1
                guess_usd = max(min(guess_usd, 50.0), -0.99)
                cashflows_usd: list[tuple[_date, float]] = [(d_inicio, +prev_val_usd)]
                if detalle:
                    cashflow_xirr_usd.append({
                        "fecha": prev_fecha, "monto": round(prev_val_usd, 2),
                        "tipo": "valor_inicio",
                    })
                for fecha_iso, imp_ars in (f.get("items") or []):
                    try:
                        if fecha_iso not in mep_cache:
                            mep_cache[fecha_iso] = _get_mep_for_date(fecha_iso)
                        mep_flujo = mep_cache.get(fecha_iso)
                        imp_usd = imp_ars / mep_flujo if mep_flujo and mep_flujo > 0 else 0.0
                        if imp_usd != 0:
                            cashflows_usd.append((_date.fromisoformat(fecha_iso), float(imp_usd)))
                            if detalle:
                                cashflow_xirr_usd.append({
                                    "fecha": fecha_iso, "monto": round(float(imp_usd), 2),
                                    "tipo": "flujo",
                                })
                    except (ValueError, TypeError):
                        continue
                cashflows_usd.append((d_cierre, -cierre_usd))
                if detalle:
                    cashflow_xirr_usd.append({
                        "fecha": ultimo_dia, "monto": round(-cierre_usd, 2),
                        "tipo": "valor_cierre",
                    })
                tea_mensual_usd = _xirr(cashflows_usd, guess=guess_usd)
                if tea_mensual_usd is not None and dias_periodo > 0:
                    r_mes_usd = (1 + tea_mensual_usd) ** (dias_periodo / 365) - 1
            except ValueError:
                tea_mensual_usd = None

        # Anchor del TWR en el primer mes con datos = 100. Después compone con
        # (1 + TEM_t). Si un mes no converge, twr_acum mantiene su último valor
        # (no rompe la serie visual).
        if not twr_iniciado:
            twr_acum = 100.0
            twr_acum_usd = 100.0
            twr_iniciado = True
        elif r_mes is not None:
            twr_acum = twr_acum * (1 + r_mes)
        if r_mes_usd is not None:
            twr_acum_usd = twr_acum_usd * (1 + r_mes_usd)

        row = {
            "mes":              mes,
            "fecha_inicio":     prev_fecha,
            "ultimo_dia":       ultimo_dia,
            "dias_periodo":     dias_periodo,
            "valor_inicio":     prev_val,
            "cierre":           cierre,
            "depositos":        depositos,
            "extracciones":     extracciones,
            "flujo_neto":       flujo_neto,
            "delta_bruto":      delta_bruto,
            "delta_real":       delta_real,
            "tea_mensual":      tea_mensual,
            "tem_periodo":      r_mes,
            "twr_acum":         twr_acum,
            "mep_cierre":       mep_cierre,
            "valor_inicio_usd": prev_val_usd,
            "cierre_usd":       cierre_usd,
            "depositos_usd":    depositos_usd,
            "extracciones_usd": extracciones_usd,
            "flujo_neto_usd":   flujo_neto_usd,
            "delta_bruto_usd":  delta_bruto_usd,
            "delta_real_usd":   delta_real_usd,
            "tea_mensual_usd":  tea_mensual_usd,
            "tem_periodo_usd":  r_mes_usd,
            "twr_acum_usd":     twr_acum_usd,
            "n_posiciones":     c.get("n_posiciones", 0),
        }
        if detalle:
            row["flujos_detalle"] = f.get("flujos_detalle") or []
            row["cashflow_xirr"] = cashflow_xirr
            row["cashflow_xirr_usd"] = cashflow_xirr_usd
        rows.append(row)
        prev_val = cierre
        prev_val_usd = cierre_usd
        prev_fecha = ultimo_dia

    return rows


def _r2(v):
    return round(v, 2) if v is not None else None


def _r6(v):
    return round(v, 6) if v is not None else None


def _valuacion_mensual(id_cuenta: str, flujos_override: dict | None = None,
                       cartera: str | None = None) -> dict[str, Any]:
    """Tabla mensual: valor al cierre del mes + flujos externos del mes.
    Métricas en ARS y USD paralelas. Wrapper de presentación sobre
    `_calcular_meses` (el cálculo vive UNA sola vez ahí).

    Returns:
      {id_cuenta, meses: [{mes, ultimo_dia, valuacion_cierre, depositos,
       extracciones, flujo_neto, delta_bruto, delta_real, tea_mensual,
       tem_periodo, twr_base100, mep_cierre, *_usd, n_posiciones}, ...]
       (mes más reciente primero), n_meses}
    """
    meses = _calcular_meses(
        id_cuenta, flujos_override=flujos_override, cartera=cartera, detalle=False,
    )
    rows = [{
        "mes":                  m["mes"],
        "ultimo_dia":           m["ultimo_dia"],
        "valuacion_cierre":     round(m["cierre"], 2),
        "depositos":            round(m["depositos"], 2),
        "extracciones":         round(m["extracciones"], 2),
        "flujo_neto":           round(m["flujo_neto"], 2),
        "delta_bruto":          _r2(m["delta_bruto"]),
        "delta_real":           _r2(m["delta_real"]),
        "tea_mensual":          _r6(m["tea_mensual"]),
        "tem_periodo":          _r6(m["tem_periodo"]),
        "twr_base100":          round(m["twr_acum"], 4),
        # USD parallels
        "mep_cierre":           round(m["mep_cierre"], 4) if m["mep_cierre"] is not None else None,
        "valuacion_cierre_usd": round(m["cierre_usd"], 2),
        "depositos_usd":        round(m["depositos_usd"], 2),
        "extracciones_usd":     round(m["extracciones_usd"], 2),
        "flujo_neto_usd":       round(m["flujo_neto_usd"], 2),
        "delta_bruto_usd":      _r2(m["delta_bruto_usd"]),
        "delta_real_usd":       _r2(m["delta_real_usd"]),
        "tea_mensual_usd":      _r6(m["tea_mensual_usd"]),
        "tem_periodo_usd":      _r6(m["tem_periodo_usd"]),
        "twr_base100_usd":      round(m["twr_acum_usd"], 4),
        "n_posiciones":         m["n_posiciones"],
    } for m in meses]
    # Orden descendente (mes más reciente primero — para UI).
    rows.reverse()
    return {
        "id_cuenta": id_cuenta,
        "meses":     rows,
        "n_meses":   len(rows),
    }


def valuacion_mensual_debug(id_cuenta: str) -> dict[str, Any]:
    """Versión expandida de `valuacion_mensual` para auditoría desde /manager.
    **No cacheada**. Mismo motor (`_calcular_meses(detalle=True)`) que
    producción — la auditoría calcula EXACTAMENTE igual, solo muestra más:
    flujos individuales, el cashflow exacto que recibe xirr() (reproducible en
    Excel con TIR.NO.PER) y valores inicio/cierre por período.
    """
    meses = _calcular_meses(id_cuenta, detalle=True)
    rows = [{
        "mes":                  m["mes"],
        "fecha_inicio":         m["fecha_inicio"],
        "fecha_cierre":         m["ultimo_dia"],
        "dias_periodo":         m["dias_periodo"],
        "valor_inicio":         _r2(m["valor_inicio"]),
        "valor_cierre":         round(m["cierre"], 2),
        "depositos":            round(m["depositos"], 2),
        "extracciones":         round(m["extracciones"], 2),
        "flujo_neto":           round(m["flujo_neto"], 2),
        "delta_bruto":          _r2(m["delta_bruto"]),
        "delta_real":           _r2(m["delta_real"]),
        "flujos_individuales":  m["flujos_detalle"],
        "cashflow_xirr":        m["cashflow_xirr"],
        "tea_mensual":          _r6(m["tea_mensual"]),
        "tem_periodo":          _r6(m["tem_periodo"]),
        "twr_base100_acum":     round(m["twr_acum"], 4),
        # USD parallels
        "mep_cierre":           round(m["mep_cierre"], 4) if m["mep_cierre"] is not None else None,
        "valor_inicio_usd":     _r2(m["valor_inicio_usd"]),
        "valor_cierre_usd":     round(m["cierre_usd"], 2),
        "depositos_usd":        round(m["depositos_usd"], 2),
        "extracciones_usd":     round(m["extracciones_usd"], 2),
        "flujo_neto_usd":       round(m["flujo_neto_usd"], 2),
        "delta_bruto_usd":      _r2(m["delta_bruto_usd"]),
        "delta_real_usd":       _r2(m["delta_real_usd"]),
        "cashflow_xirr_usd":    m["cashflow_xirr_usd"],
        "tea_mensual_usd":      _r6(m["tea_mensual_usd"]),
        "tem_periodo_usd":      _r6(m["tem_periodo_usd"]),
        "twr_base100_acum_usd": round(m["twr_acum_usd"], 4),
        "n_posiciones":         m["n_posiciones"],
    } for m in meses]
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


def aum_raw(id_cuenta: str, fecha: str | None = None) -> dict[str, Any]:
    """Filas CRUDAS de la tenencia (AuM) para una (cuenta, fecha) — desde SQL
    `portafolio.tenencia` (Valuaciones.AuM Mongo eliminada).

    Sin agrupar ni enriquecer — devuelve cada fila tal cual (unidad, cantidad,
    precio, valuacion, tipo_titulo). Pensado para validar a ojo si los precios y
    las valuaciones están bien: `valuacion` debería ser `cantidad × precio` (con
    el divisor /100 que aplique según cartera — ese cálculo lo hace el writer).

    No cacheado — siempre muestra el estado actual de la tabla. Filtra aum='si'
    (las filas que cuentan como AuM, == lo que tenía la colección vieja).

    Args:
        id_cuenta: id numérico, ej "805".
        fecha: YYYY-MM-DD. Si None o inexistente, usa el último snapshot.

    Returns:
        {
          id_cuenta, fecha, fechas_disponibles: [...],
          posiciones: [{unidad, cuenta, tipo, moneda, cantidad, precio,
                        valuacion, valuacion_esperada, desvio}, ...],
          total, n,
        }
    """
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT fecha FROM portafolio.tenencia "
            "WHERE id_cuenta = %s AND aum = 'si' ORDER BY fecha", (str(id_cuenta),))
        fechas = [f[0].isoformat() for f in cur.fetchall()]
        if not fechas:
            return {
                "id_cuenta": id_cuenta, "fecha": None, "fechas_disponibles": [],
                "posiciones": [], "total": 0.0, "n": 0,
            }
        if not fecha or fecha not in fechas:
            fecha = fechas[-1]
        cur.execute(
            "SELECT unidad, cuenta, cantidad, precio, valuacion, tipo_titulo, moneda "
            "FROM portafolio.tenencia WHERE id_cuenta = %s AND fecha = %s AND aum = 'si' "
            "ORDER BY valuacion DESC NULLS LAST", (str(id_cuenta), fecha))
        docs = [
            {"unidad": r[0], "cuenta": r[1], "cantidad": r[2], "precio": r[3],
             "valuacion": r[4], "tipoTitulo": r[5], "moneda": r[6]}
            for r in cur.fetchall()
        ]

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
            mep_cache[fecha] = _get_mep_for_date(fecha)
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
            m = valuacion_mensual(id_cuenta=str(id_cta))
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


