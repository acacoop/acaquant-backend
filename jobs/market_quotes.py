"""market_quotes.py — cotizaciones equity + forex para watchlists.

Patrón eficiente: UN poller centralizado llena `Market.Quotes` con el último
snapshot por símbolo. Los clientes (home, /renta-variable, asistente) leen
de Mongo — cero hammering adicional sobre Finnhub aunque haya muchos tabs
abiertos.

Fuentes:
- Equities (stocks, ETFs, índices vía ETF proxy): Finnhub /quote.
- Forex: frankfurter.app (ECB reference rates, gratis, sin API key).
  Finnhub free NO tiene forex.

Cron sugerido (cada 1 min en horario de mercado US, L-V):
    * 13-21 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_quotes
    * 13-21 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_quotes --extra
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

import requests

from core.finnhub import FinnhubError, quote
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

# FX — Finnhub free NO tiene forex (403). Usamos frankfurter.app (ECB, gratis,
# sin API key). Cada par se lee como base vs target.
HOME_FX: list[tuple[str, str, str, str]] = [
    # (display_symbol, base, target, grupo)
    ("EURUSD", "EUR", "USD", "Monedas"),
    ("USDBRL", "USD", "BRL", "Monedas"),
    ("USDMXN", "USD", "MXN", "Monedas"),
]

FRANKFURTER_LATEST = "https://api.frankfurter.app/latest"
FRANKFURTER_DATE   = "https://api.frankfurter.app"  # + /YYYY-MM-DD

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


def _frankfurter_rate(base: str, target: str, date: str | None = None) -> float | None:
    """Fetch 1 {base} = X {target} desde frankfurter.app.
    date=None → latest. date='YYYY-MM-DD' → histórico.
    """
    url = f"{FRANKFURTER_LATEST}" if date is None else f"{FRANKFURTER_DATE}/{date}"
    try:
        r = requests.get(url, params={"from": base, "to": target}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float((data.get("rates") or {}).get(target))
    except Exception as e:
        logger.warning("frankfurter %s→%s %s failed: %s", base, target, date or "latest", e)
        return None


def _upsert_forex(coll, display: str, base: str, target: str, grupo: str, now: datetime) -> bool:
    last = _frankfurter_rate(base, target)
    if last is None:
        return False
    # Previous close = último día hábil previo. Frankfurter NO tiene fines de
    # semana (ECB). Pedimos el día anterior hasta que haya datos.
    prev = None
    for dd in range(1, 5):
        d = (now - timedelta(days=dd)).date().isoformat()
        prev = _frankfurter_rate(base, target, d)
        if prev is not None and prev != last:
            break

    pct_day = None
    if prev:
        try:
            pct_day = (last - prev) / prev * 100
        except ZeroDivisionError:
            pass

    doc = {
        "symbol":     display,
        "base":       base,
        "target":     target,
        "type":       "forex",
        "grupo":      grupo,
        "last":       last,
        "prev_close": prev,
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

    for display, base, target, grupo in HOME_FX:
        if _upsert_forex(coll, display, base, target, grupo, now):
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
