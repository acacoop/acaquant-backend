"""backfill_precios_acciones.py — historia daily de cada activo de
Trading.Cedears en Trading.PreciosAcciones (time series).

IMPORTANTE: guarda el precio del **UNDERLYING US** (NVDA, AMD, AAPL, etc.)
en USD, NO del CEDEAR BYMA en ARS. El CEDEAR local vive en
Trading.CedearsSnapshot (motor_cedears). Esta colección es la serie
histórica del activo "real" para cálculos quant (pivots, etc.).

Fuente: core/yahoo.stock_candle (yfinance bajo el capó). Resolution "D".

Modo por default = FILL: baja `[--desde, hoy]` e inserta SOLO las fechas
que faltan. NO borra nada — es idempotente, se puede correr las veces que
haga falta. Sirve para rellenar huecos viejos (ej. la historia previa a
mayo 2025, que el backfill original de "365 días" nunca trajo).

Con --reset borra todos los docs del ticker y reinserta el rango entero.

Uso:
    python -m scripts.backfill_precios_acciones --dry-run
    python -m scripts.backfill_precios_acciones                    # fill desde 2024-01-01
    python -m scripts.backfill_precios_acciones --desde 2025-01-01  # fill desde fecha
    python -m scripts.backfill_precios_acciones --reset            # borra y rehace
    python -m scripts.backfill_precios_acciones --ticker NVDA      # uno solo
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.yahoo import YahooError, stock_candle

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DESDE_DEFAULT = "2024-01-01"


def _tickers_activos(filter_ticker: str | None = None) -> list[str]:
    """UNDERLYINGS (símbolo US para yfinance) de los CEDEARs activos.

    Para la mayoría coincide con ticker_corto; algunos argentinos tienen
    CEDEAR con sufijo distinto (YPFD CEDEAR → YPF ADR US). Se almacena en
    PreciosAcciones por underlying.
    """
    db = get_mongo_client()["Trading"]
    q: dict = {"activo": True}
    if filter_ticker:
        ft = filter_ticker.upper()
        q = {"activo": True, "$or": [{"ticker_corto": ft}, {"underlying": ft}]}
    underlyings = set()
    for d in db["Cedears"].find(q, {"_id": 0, "ticker_corto": 1, "underlying": 1}):
        underlyings.add(d.get("underlying") or d["ticker_corto"])
    return sorted(underlyings)


def _bajar_velas(ticker: str, start_dt: datetime, end_dt: datetime) -> tuple[list[dict], str | None]:
    """Baja velas daily de Yahoo en el rango. Returns (docs, error_msg)."""
    try:
        res = stock_candle(ticker, "D", int(start_dt.timestamp()), int(end_dt.timestamp()))
    except YahooError as e:
        return [], f"yahoo: {e}"
    if res.get("s") != "ok":
        return [], f"status={res.get('s')}"

    times  = res.get("t") or []
    opens  = res.get("o") or []
    highs  = res.get("h") or []
    lows   = res.get("l") or []
    closes = res.get("c") or []
    vols   = res.get("v") or []

    docs: list[dict] = []
    for i in range(len(times)):
        # Yahoo manda fechas en epoch a las 00:00 del día → preservamos.
        docs.append({
            "fecha":  datetime.fromtimestamp(times[i], tz=UTC),
            "ticker": ticker,
            "open":   opens[i]  if i < len(opens)  else None,
            "high":   highs[i]  if i < len(highs)  else None,
            "low":    lows[i]   if i < len(lows)   else None,
            "close":  closes[i] if i < len(closes) else None,
            "volume": vols[i]   if i < len(vols)   else None,
        })
    return docs, None


def procesar_ticker(
    col, ticker: str, start_dt: datetime, end_dt: datetime, reset: bool,
) -> tuple[int, int, str | None]:
    """Returns (n_insertados, n_ya_estaban, error_msg)."""
    docs, err = _bajar_velas(ticker, start_dt, end_dt)
    if err:
        return 0, 0, err
    if not docs:
        return 0, 0, "sin velas"

    if reset:
        col.delete_many({"ticker": ticker})
        col.insert_many(docs, ordered=False)
        return len(docs), 0, None

    # FILL — inserta solo las fechas que no están. Time series Mongo no
    # soporta upsert por (ticker, fecha), así que chequeamos con find_one
    # (deja que Mongo resuelva la igualdad de datetime — evita el lío de
    # naive vs aware al comparar sets en memoria).
    nuevos: list[dict] = []
    existentes = 0
    for d in docs:
        ya = col.find_one({"ticker": ticker, "fecha": d["fecha"]}, {"_id": 1}) is not None
        if ya:
            existentes += 1
        else:
            nuevos.append(d)
    if nuevos:
        col.insert_many(nuevos, ordered=False)
    return len(nuevos), existentes, None


def run(
    desde: str = _DESDE_DEFAULT,
    reset: bool = False,
    dry_run: bool = False,
    filter_ticker: str | None = None,
) -> None:
    tickers = _tickers_activos(filter_ticker)
    start_dt = datetime.fromisoformat(desde).replace(tzinfo=UTC)
    end_dt = datetime.now(UTC)

    print("=" * 80)
    print(f"BACKFILL Trading.PreciosAcciones — {len(tickers)} tickers")
    print(f"Rango: {start_dt.date()} → {end_dt.date()}")
    print(f"Modo: {'DRY-RUN' if dry_run else ('RESET (borra y rehace)' if reset else 'FILL (inserta faltantes)')}")
    print("=" * 80)

    if dry_run:
        for t in tickers:
            print(f"  [DRY] {t}")
        return

    db = get_mongo_client()["Trading"]
    if "PreciosAcciones" not in db.list_collection_names():
        print("  ✗ Trading.PreciosAcciones no existe. Correr scripts/setup_precios_acciones.py")
        return
    col = db["PreciosAcciones"]

    total_ins = 0
    total_exist = 0
    errors = 0
    for ticker in tickers:
        ins, exist, err = procesar_ticker(col, ticker, start_dt, end_dt, reset)
        if err:
            print(f"  ✗ {ticker:6s} FAIL: {err}")
            errors += 1
        else:
            print(f"  ✓ {ticker:6s} +{ins} nuevos, {exist} ya estaban")
            total_ins += ins
            total_exist += exist
        # Anti rate-limit yfinance (Yahoo throttle ~2000 req/h por IP).
        time.sleep(0.3)

    print(f"\nResumen: {total_ins:,} insertados · {total_exist:,} ya estaban · {errors} con error")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=_DESDE_DEFAULT,
                    help=f"YYYY-MM-DD inicio del backfill (default {_DESDE_DEFAULT})")
    ap.add_argument("--reset", action="store_true", help="Borra y rehace cada ticker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ticker", help="Backfill solo un ticker (debug)")
    args = ap.parse_args()
    run(desde=args.desde, reset=args.reset, dry_run=args.dry_run, filter_ticker=args.ticker)
