"""backfill_precios_acciones.py — baja 365 ruedas daily de cada activo
de Trading.Cedears y las guarda en Trading.PreciosAcciones (time series).

IMPORTANTE: guarda el precio del **UNDERLYING US** (NVDA, AMD, AAPL, etc.)
en USD, NO del CEDEAR BYMA en ARS. El CEDEAR local sigue en
Trading.CedearsSnapshot (escrito por motor_cedears). Esta colección es la
serie histórica del activo "real" para cálculos quant.

Fuente: core/yahoo.stock_candle (yfinance bajo el capó). Resolution "D"
(diaria), rango = hoy − 365d → hoy.

Idempotencia: para cada (ticker, fecha) NO upsertea (time series Mongo
no permite update sobre timeField). En su lugar:
  - Si --reset: borra todos los docs del ticker primero y reinserta.
  - Si NO --reset: chequea si ya existe doc para ese ticker en cualquier
    fecha; si sí, skip (asume backfill ya corrió). Para refrescar usar
    --reset.

Uso:
    python -m scripts.backfill_precios_acciones --dry-run    # solo lista
    python -m scripts.backfill_precios_acciones              # backfill 365d
    python -m scripts.backfill_precios_acciones --reset      # borra y rehace
    python -m scripts.backfill_precios_acciones --ticker NVDA  # uno solo
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


def _tickers_activos(filter_ticker: str | None = None) -> list[str]:
    db = get_mongo_client()["Trading"]
    q = {"activo": True}
    if filter_ticker:
        q["ticker_corto"] = filter_ticker.upper()
    return sorted(
        d["ticker_corto"]
        for d in db["Cedears"].find(q, {"_id": 0, "ticker_corto": 1})
    )


def backfill_ticker(col, ticker: str, dias: int = 365) -> tuple[int, str | None]:
    """Returns (n_docs_insertados, error_msg)."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=dias)

    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return 0, f"yahoo: {e}"

    if res.get("s") != "ok":
        return 0, f"status={res.get('s')}"

    times = res.get("t") or []
    opens = res.get("o") or []
    highs = res.get("h") or []
    lows  = res.get("l") or []
    closes = res.get("c") or []
    vols  = res.get("v") or []

    n = len(times)
    if n == 0:
        return 0, "sin velas"

    docs = []
    for i in range(n):
        # Yahoo manda fechas en epoch a las 00:00 del día → preservamos.
        fecha = datetime.fromtimestamp(times[i], tz=UTC)
        docs.append({
            "fecha":  fecha,
            "ticker": ticker,
            "open":   opens[i] if i < len(opens) else None,
            "high":   highs[i] if i < len(highs) else None,
            "low":    lows[i]  if i < len(lows)  else None,
            "close":  closes[i] if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
        })

    col.insert_many(docs, ordered=False)
    return len(docs), None


def run(reset: bool = False, dry_run: bool = False, filter_ticker: str | None = None) -> None:
    tickers = _tickers_activos(filter_ticker)
    print("=" * 80)
    print(f"BACKFILL Trading.PreciosAcciones — {len(tickers)} tickers")
    print(f"Modo: {'DRY-RUN' if dry_run else ('RESET' if reset else 'INSERT IF EMPTY')}")
    print("=" * 80)

    if dry_run:
        for t in tickers:
            print(f"  [DRY] backfill {t} (365 días)")
        return

    client = get_mongo_client()
    db = client["Trading"]
    if "PreciosAcciones" not in db.list_collection_names():
        print("  ✗ Trading.PreciosAcciones no existe. Correr scripts/setup_precios_acciones.py")
        return
    col = db["PreciosAcciones"]

    total_inserted = 0
    errors = 0
    for ticker in tickers:
        # Chequear si ya hay data para este ticker.
        # NOTA: time series no soporta count_documents con $exists eficiente,
        # pero find_one con limit=1 sí.
        ya_existe = col.find_one({"ticker": ticker}, {"_id": 1}) is not None

        if ya_existe and not reset:
            print(f"  · {ticker:6s} ya tiene data — skip (usar --reset para rehacer)")
            continue

        if ya_existe and reset:
            res = col.delete_many({"ticker": ticker})
            print(f"  ↻ {ticker:6s} reset: borrados {res.deleted_count} docs")

        n, err = backfill_ticker(col, ticker, dias=365)
        if err:
            print(f"  ✗ {ticker:6s} FAIL: {err}")
            errors += 1
        else:
            print(f"  ✓ {ticker:6s} {n} velas insertadas")
            total_inserted += n
        # Anti rate-limit yfinance (Yahoo throttle ~2000 req/h por IP).
        time.sleep(0.3)

    print(f"\nResumen: {total_inserted:,} docs insertados · {errors} con error")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="Borra y rehace cada ticker")
    ap.add_argument("--ticker", help="Backfill solo un ticker (debug)")
    args = ap.parse_args()
    run(reset=args.reset, dry_run=args.dry_run, filter_ticker=args.ticker)
