"""Pivots Floor Trader sobre el CEDEAR (ARS) — vista TRADING.

Toma el último OHLC diario COMPLETO de cada CEDEAR (mercado.cedears_ohlc_daily,
que escribe jobs.cedears_ohlc_daily tras el cierre) y calcula los 7 niveles
(PP, R1-R3, S1-S3) con `quant.pivot_points.calcular`. Suma el `last` live del
snapshot para los modos "diferencia vs last" del frontend.

Si la tabla todavía no tiene una rueda para un ticker → {sin_datos: true} (la
tabla se llena hacia adelante: ver [[project_vista_trading]]).
"""
from __future__ import annotations

import logging

from psycopg.rows import dict_row

from api.services import scanner_sql as scanner_svc
from core.postgres import get_pool
from quant.pivot_points import calcular

logger = logging.getLogger(__name__)


def _ohlc_ultima_rueda(tickers: list[str]) -> dict[str, dict]:
    """{ticker_corto: {fecha, high, low, close}} de la rueda más reciente guardada."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker_corto)
                   ticker_corto, fecha, high, low, close
            FROM mercado.cedears_ohlc_daily
            WHERE ticker_corto = ANY(%s)
            ORDER BY ticker_corto, fecha DESC
            """,
            (tickers,),
        )
        return {r["ticker_corto"]: r for r in cur.fetchall()}


def _last_por_ticker(tickers: set[str]) -> dict[str, float | None]:
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


def get_pivots(*, tickers: list[str]) -> list[dict]:
    """Pivots del CEDEAR por ticker (orden pedido). last live + niveles sobre la
    última rueda guardada."""
    tks = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    if not tks:
        return []
    ohlc = _ohlc_ultima_rueda(tks)
    lasts = _last_por_ticker(set(tks))

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
