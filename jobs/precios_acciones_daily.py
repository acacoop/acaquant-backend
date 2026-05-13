"""precios_acciones_daily.py — agrega 1 vela daily por activo a
Trading.PreciosAcciones.

Diseñado para correr 1×/día post-cierre US. Para cada CEDEAR activo:
  1. Pide a Yahoo los últimos 5 días (D, OHLCV).
  2. Insertar SOLO las fechas que NO están ya en la colección (idempotente).
     Time series Mongo no soporta upsert por (ticker, fecha) → chequeamos
     existencia antes de cada insert.

5 días de colchón: si el cron falló un día/festivo, recuperamos esos días
en la próxima corrida sin sumar lógica extra.

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

from core.mongo import get_mongo_client
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DIAS_COLCHON = 5  # cuántos días pedir hacia atrás para recuperar gaps


def _tickers_activos(filter_ticker: str | None = None) -> list[str]:
    db = get_mongo_client()["Trading"]
    q = {"activo": True}
    if filter_ticker:
        q["ticker_corto"] = filter_ticker.upper()
    return sorted(
        d["ticker_corto"]
        for d in db["Cedears"].find(q, {"_id": 0, "ticker_corto": 1})
    )


def upsert_ticker(col, ticker: str) -> tuple[int, int, str | None]:
    """Returns (n_insertados, n_existentes, error_msg)."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=DIAS_COLCHON)

    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return 0, 0, f"yahoo: {e}"

    if res.get("s") != "ok":
        return 0, 0, f"status={res.get('s')}"

    times  = res.get("t") or []
    opens  = res.get("o") or []
    highs  = res.get("h") or []
    lows   = res.get("l") or []
    closes = res.get("c") or []
    vols   = res.get("v") or []
    n = len(times)
    if n == 0:
        return 0, 0, "sin velas"

    insertados = 0
    existentes = 0
    for i in range(n):
        fecha = datetime.fromtimestamp(times[i], tz=UTC)
        # Chequear si existe — time series usa _id interno, no podemos
        # upsertear por (ticker, fecha). Aceptamos la latencia del find.
        already = col.find_one({"ticker": ticker, "fecha": fecha}, {"_id": 1}) is not None
        if already:
            existentes += 1
            continue
        col.insert_one({
            "fecha":  fecha,
            "ticker": ticker,
            "open":   opens[i] if i < len(opens) else None,
            "high":   highs[i] if i < len(highs) else None,
            "low":    lows[i]  if i < len(lows)  else None,
            "close":  closes[i] if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
        })
        insertados += 1

    return insertados, existentes, None


def run(filter_ticker: str | None = None) -> None:
    tickers = _tickers_activos(filter_ticker)
    logger.info("precios_acciones_daily — %d tickers", len(tickers))

    client = get_mongo_client()
    col = client["Trading"]["PreciosAcciones"]

    total_new = 0
    total_existing = 0
    errors = 0
    for t in tickers:
        ins, exist, err = upsert_ticker(col, t)
        if err:
            logger.warning("%s FAIL: %s", t, err)
            errors += 1
        else:
            logger.info("%-6s +%d nuevos, %d ya estaban", t, ins, exist)
            total_new += ins
            total_existing += exist
        time.sleep(0.3)  # anti rate-limit yfinance

    logger.info(
        "Resumen: %d nuevos · %d ya estaban · %d errores",
        total_new, total_existing, errors,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="Solo un ticker (debug)")
    args = ap.parse_args()
    run(filter_ticker=args.ticker)
