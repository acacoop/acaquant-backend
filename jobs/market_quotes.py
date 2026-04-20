"""market_quotes.py — cotizaciones equity + forex para watchlists.

Patrón eficiente: UN poller centralizado llena `Market.Quotes` con el último
snapshot por símbolo. Los clientes (home, /renta-variable, asistente) leen
de Mongo — cero hammering adicional sobre Finnhub aunque haya muchos tabs
abiertos.

Cron sugerido (cada 1 min en horario de mercado US, L-V):
    * 13-21 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_quotes
    * 13-21 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_quotes --extra  # incluye ADRs

En horario no-mercado, el último snapshot persiste. No es crítico refrescar
overnight.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from core.finnhub import FinnhubError, forex_candle, quote
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

# ── Watchlist HOME — ~18 tickers (panel widget) ──
HOME_STOCKS: list[tuple[str, str]] = [
    # (símbolo, grupo)
    ("SPY",  "Índices"),
    ("QQQ",  "Índices"),
    ("DIA",  "Índices"),
    ("IWM",  "Índices"),
    ("ARGT", "Regiones"),
    ("EWZ",  "Regiones"),
    ("EEM",  "Regiones"),
    ("EWW",  "Regiones"),
    ("GLD",  "Commodities"),
    ("SLV",  "Commodities"),
    ("USO",  "Commodities"),
    ("UNG",  "Commodities"),
    ("CORN", "Commodities"),
    ("SOYB", "Commodities"),
    ("WEAT", "Commodities"),
]

HOME_FX: list[tuple[str, str, str]] = [
    # (display_symbol, finnhub_symbol, grupo)
    ("EURUSD", "OANDA:EUR_USD", "Monedas"),
    ("USDBRL", "OANDA:USD_BRL", "Monedas"),
    ("USDMXN", "OANDA:USD_MXN", "Monedas"),
]

# Watchlist ampliada para /renta-variable
EXTRA_STOCKS: list[tuple[str, str]] = [
    ("GGAL",  "ADR Argentina"),
    ("YPF",   "ADR Argentina"),
    ("BMA",   "ADR Argentina"),
    ("BBAR",  "ADR Argentina"),
    ("TGS",   "ADR Argentina"),
    ("PAM",   "ADR Argentina"),
    ("LOMA",  "ADR Argentina"),
    ("CRESY", "ADR Argentina"),
    ("IRS",   "ADR Argentina"),
    ("EDN",   "ADR Argentina"),
    ("CEPU",  "ADR Argentina"),
    ("TEO",   "ADR Argentina"),
    ("SUPV",  "ADR Argentina"),
    ("VIST",  "ADR Argentina"),
    # LATAM referencia
    ("VALE", "ADR LATAM"),
    ("ITUB", "ADR LATAM"),
    ("PBR",  "ADR LATAM"),
    ("AMX",  "ADR LATAM"),
    # Big tech US (contexto)
    ("AAPL", "Big Tech"),
    ("MSFT", "Big Tech"),
    ("NVDA", "Big Tech"),
    ("GOOGL", "Big Tech"),
    ("AMZN", "Big Tech"),
    ("META", "Big Tech"),
    ("TSLA", "Big Tech"),
]


def _upsert_stock(coll, sym: str, grupo: str, q: dict, now: datetime) -> bool:
    if not q or q.get("c") in (None, 0):
        return False
    last       = q.get("c")
    prev_close = q.get("pc")
    pct_day    = None
    if last is not None and prev_close:
        try:
            pct_day = (last - prev_close) / prev_close * 100
        except (TypeError, ZeroDivisionError):
            pct_day = None
    doc = {
        "symbol":     sym,
        "type":       "stock",
        "grupo":      grupo,
        "last":       last,
        "open":       q.get("o"),
        "high":       q.get("h"),
        "low":        q.get("l"),
        "prev_close": prev_close,
        "pct_day":    pct_day,
        "timestamp":  datetime.fromtimestamp(q["t"], tz=timezone.utc) if q.get("t") else now,
        "updated_at": now,
    }
    coll.update_one({"symbol": sym}, {"$set": doc}, upsert=True)
    return True


def _upsert_forex(coll, display: str, fh_sym: str, grupo: str, now: datetime) -> bool:
    # Usamos /forex/candle con resolución horaria de las últimas 48h para
    # obtener last + first-of-day.
    now_ts = int(now.timestamp())
    try:
        c = forex_candle(fh_sym, "60", now_ts - 2 * 86400, now_ts)
    except FinnhubError as e:
        logger.warning("forex %s failed: %s", fh_sym, e)
        return False
    if c.get("s") != "ok":
        return False
    closes = c.get("c") or []
    times = c.get("t") or []
    if not closes:
        return False
    last = closes[-1]
    # first-of-day = la primera vela con timestamp de hoy UTC
    hoy_ts = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp())
    first_today = None
    for t, cl in zip(times, closes):
        if t >= hoy_ts:
            first_today = cl
            break
    if first_today is None:
        first_today = closes[0]
    pct_day = None
    if first_today:
        try:
            pct_day = (last - first_today) / first_today * 100
        except ZeroDivisionError:
            pct_day = None
    doc = {
        "symbol":     display,
        "fh_symbol":  fh_sym,
        "type":       "forex",
        "grupo":      grupo,
        "last":       last,
        "prev_close": first_today,
        "pct_day":    pct_day,
        "timestamp":  now,
        "updated_at": now,
    }
    coll.update_one({"symbol": display}, {"$set": doc}, upsert=True)
    return True


def ingesta(include_extra: bool = False) -> int:
    client = get_mongo_client()
    coll = client["Market"]["Quotes"]
    coll.create_index("symbol", unique=True)

    now = datetime.now(timezone.utc)
    stocks = HOME_STOCKS + (EXTRA_STOCKS if include_extra else [])

    ok = fail = 0
    for sym, grupo in stocks:
        try:
            q = quote(sym)
        except FinnhubError as e:
            logger.warning("quote %s failed: %s", sym, e)
            fail += 1
            continue
        if _upsert_stock(coll, sym, grupo, q, now):
            ok += 1
        else:
            fail += 1

    for display, fh_sym, grupo in HOME_FX:
        if _upsert_forex(coll, display, fh_sym, grupo, now):
            ok += 1
        else:
            fail += 1

    logger.info("market_quotes — ok=%d fail=%d stocks=%d fx=%d",
                ok, fail, len(stocks), len(HOME_FX))
    return 0 if fail < ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", action="store_true", help="Incluir ADRs + Big Tech (/renta-variable)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ingesta(include_extra=args.extra)


if __name__ == "__main__":
    sys.exit(main())
