"""Pivots Floor Trader sobre el activo (ARS) — vista TRADING.

Soporta DOS clases de activo, resueltas por su master:
  • CEDEARs → OHLC diario de mercado.cedears_ohlc_daily (jobs.cedears_ohlc_daily),
    last live del scanner (mercado.cedears_snapshot).
  • Bonos (renta fija, mercado.curvas) → OHLC diario de mercado.bonos_ohlc_daily
    (jobs.bonos_ohlc_daily), last live de mercado.market_snapshot.

Las dos tablas de OHLC tienen el MISMO shape (ticker_corto, fecha, high, low,
close) → el cálculo de los 7 niveles (PP, R1-R3, S1-S3) con
`quant.pivot_points.calcular` es idéntico. La única diferencia es de qué tabla
sale el último cierre guardado y de dónde el `last` live.

Si la tabla todavía no tiene una rueda para un ticker → {sin_datos: true} (las
tablas se llenan hacia adelante: ver [[project_vista_trading]]). Con el bono
igual llega el `last` live, así la card muestra precio desde el día 0.
"""
from __future__ import annotations

import logging

from psycopg.rows import dict_row

from api.services import scanner_sql as scanner_svc
from core import curvas_sql, market_snapshot
from core.postgres import get_pool
from quant.pivot_points import calcular

logger = logging.getLogger(__name__)


def _ohlc_ultima_rueda(tabla: str, tickers: list[str]) -> dict[str, dict]:
    """{ticker_corto: {fecha, high, low, close}} de la rueda más reciente guardada.
    `tabla` es un literal interno (cedears/bonos), NO input de usuario."""
    if not tickers:
        return {}
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (ticker_corto)
                       ticker_corto, fecha, high, low, close
                FROM {tabla}
                WHERE ticker_corto = ANY(%s)
                ORDER BY ticker_corto, fecha DESC
                """,
                (tickers,),
            )
            return {r["ticker_corto"]: r for r in cur.fetchall()}
    except Exception as e:
        # La tabla se crea hacia adelante (DDL del job en su primer run). Si aún no
        # existe → sin OHLC guardado = sin_datos (el `last` live igual llega aparte).
        logger.debug("trading_pivots: %s no disponible (%s)", tabla, e)
        return {}


def _cedears_last(tickers: set[str]) -> dict[str, float | None]:
    """`last` live de cada CEDEAR desde el scanner (1 llamada, @cached)."""
    try:
        universo = scanner_svc.get_cedears_scanner()
    except Exception as e:
        logger.debug("trading_pivots: scanner falló (%s)", e)
        return {}
    out: dict[str, float | None] = {}
    for r in universo:
        tk = str(r.get("ticker_corto", "")).upper()
        if tk in tickers:
            out[tk] = r.get("last")
    return out


def _bonos_corto_a_largo() -> dict[str, str]:
    """{ticker_corto(upper): ticker_largo ROFEX} del master de renta fija (cacheado 300s)."""
    out: dict[str, str] = {}
    for d in curvas_sql.cargar_todos():
        tc, tk = d.get("ticker_corto"), d.get("ticker")
        if tc and tk:
            out[str(tc).upper()] = tk
    return out


def _bonos_last(tickers: list[str], corto_a_largo: dict[str, str]) -> dict[str, float | None]:
    """`last` live de cada bono desde mercado.market_snapshot (keyeado por ticker largo)."""
    largos = [corto_a_largo[t] for t in tickers if t in corto_a_largo]
    lp = market_snapshot.metric_map(largos, "last_price", positivo=True)  # {largo: last}
    out: dict[str, float | None] = {}
    for t in tickers:
        largo = corto_a_largo.get(t)
        if largo is not None:
            out[t] = lp.get(largo)
    return out


_CURVA_LABEL = {"tasa_fija": "Tasa Fija", "cer": "CER", "soberanos": "Soberano"}


def _curva_label(curva: str | None) -> str:
    """Etiqueta corta de la curva para el selector (AL30 → 'Soberano')."""
    if not curva:
        return "Bono"
    if curva.startswith("on"):
        return "ON"
    return _CURVA_LABEL.get(curva, curva.replace("_", " ").title())


def bonos_universo() -> list[dict]:
    """Catálogo liviano de bonos de renta fija (mercado.curvas) para el selector.
    Una fila por bono: {ticker_corto, nombre (curva), clase='bono'}."""
    out: list[dict] = []
    for d in curvas_sql.cargar_todos():
        tc = d.get("ticker_corto")
        if not tc:
            continue
        out.append({
            "ticker_corto": tc,
            "nombre": _curva_label(d.get("curva")),
            "clase": "bono",
        })
    out.sort(key=lambda x: x["ticker_corto"] or "")
    return out


def get_pivots(*, tickers: list[str]) -> list[dict]:
    """Pivots por ticker (orden pedido). last live + niveles sobre la última rueda
    guardada. Resuelve CEDEAR vs bono por el master de renta fija."""
    tks = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    if not tks:
        return []

    corto_a_largo = _bonos_corto_a_largo()
    bonos = [t for t in tks if t in corto_a_largo]
    cedears = [t for t in tks if t not in corto_a_largo]

    ohlc: dict[str, dict] = {}
    lasts: dict[str, float | None] = {}
    if cedears:
        ohlc.update(_ohlc_ultima_rueda("mercado.cedears_ohlc_daily", cedears))
        lasts.update(_cedears_last(set(cedears)))
    if bonos:
        ohlc.update(_ohlc_ultima_rueda("mercado.bonos_ohlc_daily", bonos))
        lasts.update(_bonos_last(bonos, corto_a_largo))

    out: list[dict] = []
    for tk in tks:
        last = lasts.get(tk)
        row = ohlc.get(tk)
        if not row:
            out.append({"ticker": tk, "last": last, "sin_datos": True})
            continue
        h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        niveles = calcular(high=h, low=l, close=c)
        fecha = row.get("fecha")
        out.append({
            "ticker": tk,
            "fecha": fecha.isoformat() if hasattr(fecha, "isoformat") else None,
            "high": h, "low": l, "close": c,
            "last": last,
            "pivots": dict(niveles),  # pp, r1, r2, r3, s1, s2, s3
        })
    return out
