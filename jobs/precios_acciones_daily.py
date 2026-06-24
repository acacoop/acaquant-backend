"""precios_acciones_daily.py — agrega 1 vela daily por activo a
mercado.precios_acciones (SQL-native, sin Mongo).

Diseñado para correr 1×/día post-cierre US. Para cada CEDEAR activo pide a Yahoo
los últimos 5 días (D, OHLCV) y los UPSERTEA por (ticker, fecha) en
mercado.precios_acciones (idempotente — re-correr no duplica). El universo sale
de mercado.cedears (espejo SQL del master, activo=true).

5 días de colchón: si el cron falló un día/festivo, recuperamos esos días
en la próxima corrida sin sumar lógica extra.

Cutover SQL-native 2026-06-24: antes escribía Trading.PreciosAcciones (Mongo) +
espejo SQL; ahora escribe SOLO SQL (`pg_mirror.write_native`). Lectores (scanner,
quant/pivot_points) leen SQL. Sin sync ni Mongo. Ver docs/SQL.md.

Cron:
    0 22 * * 1-5  python -m jobs.precios_acciones_daily

Uso manual:
    python -m jobs.precios_acciones_daily               # corrida normal
    python -m jobs.precios_acciones_daily --ticker NVDA # uno solo
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from core import pg_mirror
from core.postgres import get_pool
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DIAS_COLCHON = 5  # cuántos días pedir hacia atrás para recuperar gaps


def _underlyings_activos(filter_ticker: str | None = None) -> list[str]:
    """UNDERLYINGS (símbolo US) de los CEDEARs activos, desde mercado.cedears (SQL)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        if filter_ticker:
            ft = filter_ticker.upper()
            cur.execute(
                "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
                "FROM mercado.cedears WHERE activo IS TRUE "
                "AND (upper(ticker_corto) = %s OR upper(underlying) = %s)", (ft, ft))
        else:
            cur.execute(
                "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
                "FROM mercado.cedears WHERE activo IS TRUE")
        return sorted(r[0] for r in cur.fetchall())


def upsert_ticker(ticker: str) -> tuple[int, str | None]:
    """Pide las velas a Yahoo y las upsertea en SQL. Returns (n_filas, error_msg)."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=DIAS_COLCHON)

    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return 0, f"yahoo: {e}"
    if res.get("s") != "ok":
        return 0, f"status={res.get('s')}"

    times = res.get("t") or []
    if not times:
        return 0, "sin velas"
    o, h, lo, c, v = (res.get(k) or [] for k in ("o", "h", "l", "c", "v"))
    rows = [{
        "ticker": ticker,
        "fecha":  datetime.fromtimestamp(t, tz=UTC).date(),
        "open":   o[i]  if i < len(o)  else None,
        "high":   h[i]  if i < len(h)  else None,
        "low":    lo[i] if i < len(lo) else None,
        "close":  c[i]  if i < len(c)  else None,
        "volume": v[i]  if i < len(v)  else None,
    } for i, t in enumerate(times)]
    # SQL-native: upsert incondicional por (ticker, fecha). Idempotente.
    pg_mirror.write_native("mercado.precios_acciones", ["ticker", "fecha"], rows)
    return len(rows), None


def run(filter_ticker: str | None = None) -> None:
    tickers = _underlyings_activos(filter_ticker)
    logger.info("precios_acciones_daily — %d tickers", len(tickers))

    total = 0
    errors = 0
    for t in tickers:
        n, err = upsert_ticker(t)
        if err:
            logger.warning("%-6s FAIL: %s", t, err)
            errors += 1
        else:
            logger.info("%-6s %d velas upserteadas", t, n)
            total += n
        time.sleep(0.3)  # anti rate-limit yfinance

    logger.info("Resumen: %d velas upserteadas · %d errores", total, errors)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="Solo un ticker (debug)")
    args = ap.parse_args()
    run(filter_ticker=args.ticker)
