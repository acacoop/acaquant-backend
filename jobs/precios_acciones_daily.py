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

from core import pg_mirror
from core.mongo import get_mongo_client
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DIAS_COLCHON = 5  # cuántos días pedir hacia atrás para recuperar gaps


def _tickers_activos(filter_ticker: str | None = None) -> list[str]:
    """Devuelve UNDERLYINGS (símbolo US) — ver doc en
    scripts/backfill_precios_acciones.py."""
    db = get_mongo_client()["Trading"]
    q: dict = {"activo": True}
    if filter_ticker:
        ft = filter_ticker.upper()
        q = {"activo": True, "$or": [{"ticker_corto": ft}, {"underlying": ft}]}
    underlyings = set()
    for d in db["Cedears"].find(q, {"_id": 0, "ticker_corto": 1, "underlying": 1}):
        underlyings.add(d.get("underlying") or d["ticker_corto"])
    return sorted(underlyings)


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

    # Existencia en UNA query (no un find_one por vela = N+1): traemos las fechas
    # ya guardadas de este ticker y chequeamos en memoria. Después un solo
    # insert_many con las nuevas (en vez de N insert_one).
    fechas = [datetime.fromtimestamp(t, tz=UTC) for t in times]
    ya_guardadas = {
        d["fecha"]
        for d in col.find(
            {"ticker": ticker, "fecha": {"$in": fechas}}, {"_id": 0, "fecha": 1}
        )
    }
    nuevos = []
    for i, fecha in enumerate(fechas):
        if fecha in ya_guardadas:
            continue
        nuevos.append({
            "fecha":  fecha,
            "ticker": ticker,
            "open":   opens[i] if i < len(opens) else None,
            "high":   highs[i] if i < len(highs) else None,
            "low":    lows[i]  if i < len(lows)  else None,
            "close":  closes[i] if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
        })
    if nuevos:
        col.insert_many(nuevos)
        # Espejo SQL (flag MERCADO_SQL_WRITE, best-effort): mismas velas nuevas a
        # mercado.precios_acciones (columnar). `fecha` Mongo (datetime naive 00h
        # UTC US) → date para la tabla. Idempotente: upsert por (ticker, fecha).
        sql_rows = [{
            "ticker": ticker,
            "fecha":  d["fecha"].date() if hasattr(d["fecha"], "date") else d["fecha"],
            "open":   d.get("open"),
            "high":   d.get("high"),
            "low":    d.get("low"),
            "close":  d.get("close"),
            "volume": d.get("volume"),
        } for d in nuevos]
        pg_mirror.mirror_job("mercado.precios_acciones", ["ticker", "fecha"], sql_rows)

    return len(nuevos), n - len(nuevos), None


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
