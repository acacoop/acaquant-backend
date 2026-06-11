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
from datetime import UTC, datetime, timedelta

import requests

from core.finnhub import FinnhubError, quote
from core.mongo import get_mongo_client
from core.pg_mirror import doc_iso, jobs_on, mirror_job
from core.yahoo import YahooError, yahoo_quote

logger = logging.getLogger(__name__)


def mirror_quotes_sql(coll) -> None:
    """Espejo SQL del watchlist COMPLETO (~20 docs) tras la ingesta. Se re-lee el
    doc final de Mongo (los upserts del job son $set parciales por símbolo —
    espejar el doc entero garantiza PG == Mongo). Flag MERCADO_SQL_WRITE, no-op
    apagado. Símbolos purgados: los limpia el delete-orphans de sync_postgres.
    También lo invoca jobs.market_anchors (escribe en la misma colección)."""
    if not jobs_on():
        return
    rows = [{"symbol": d["symbol"], "grupo": d.get("grupo"), "data": doc_iso(d)}
            for d in coll.find({}, {"_id": 0}) if d.get("symbol")]
    mirror_job("market_quotes", ["symbol"], rows)

# ── Watchlist HOME — equity/ETFs (panel widget) ──
# Grupos:
#   Índices: ETFs de mercado (S&P500, Nasdaq, Dow, etc.)
#   Acciones: tickers individuales que la mesa quiere monitorear
# (Regiones/Monedas/Big Tech viejos quedaron afuera por pedido.)
HOME_STOCKS: list[tuple[str, str]] = [
    # (símbolo, grupo)
    # 2026-05-13: sección "Acciones" eliminada del watchlist — toda esa
    # data se concentró en /renta-variable (Scanner). Quedan solo los
    # ETFs/índices que el watchlist sigue mostrando.
    ("SPY",  "Índices"),
    ("QQQ",  "Índices"),
    ("DIA",  "Índices"),
    ("IWM",  "Índices"),
    ("EWZ",  "Índices"),
]

# ── Futuros CME / CBOT / COMEX / NYMEX / ICE + cripto spot vía Yahoo.
# Equity/comms: '=F' indica continuous front-month (proxy del mes activo).
# Cripto: BTC-USD / ETH-USD son spot (Yahoo agrega varios exchanges, ~24/7).
HOME_FUTUROS: list[tuple[str, str, str]] = [
    # (yahoo_symbol, display_label, exchange_label)
    ("ES=F",    "S&P FUT",   "CME"),
    ("NQ=F",    "NASDAQ FUT","CME"),
    ("CL=F",    "WTI",       "NYMEX"),
    ("BZ=F",    "BRENT",     "ICE"),
    ("GC=F",    "ORO",       "COMEX"),
    ("ZS=F",    "SOJA",      "CBOT"),
    ("ZC=F",    "MAIZ",      "CBOT"),
    ("ZW=F",    "TRIGO",     "CBOT"),
    ("BTC-USD", "BTCUSDT",   "BINANCE"),
    ("ETH-USD", "ETHUSDT",   "BINANCE"),
]

# FX — la mesa pidió sacar las monedas de la watchlist (no aportaba).
# Si querés volver a habilitar EURUSD/USDBRL/USDMXN, sumalos acá; el
# fetcher (frankfurter.app, ECB, gratis sin API key) ya estaba listo.
HOME_FX: list[tuple[str, str, str, str]] = []

# US Treasury yields vía Yahoo (^IRX 13w, ^FVX 5y, ^TNX 10y, ^TYX 30y).
# Finnhub free no cotiza yields. Yahoo los expone como "^" index tickers.
HOME_TREASURIES: list[tuple[str, str]] = [
    # (yahoo_symbol, display_label)
    ("^IRX", "UST 13W"),
    ("^FVX", "UST 5Y"),
    ("^TNX", "UST 10Y"),
    ("^TYX", "UST 30Y"),
]

# Índices locales/LATAM que Finnhub free no cotiza. Yahoo los tiene como
# "^" tickers. Se guardan como type="index" con el display_label.
HOME_INDICES_YAHOO: list[tuple[str, str, str]] = [
    # (yahoo_symbol, display_label, grupo)
    ("^MERV", "MERVAL", "Índices"),
]

FRANKFURTER_LATEST = "https://api.frankfurter.app/latest"
FRANKFURTER_DATE   = "https://api.frankfurter.app"  # + /YYYY-MM-DD

# Watchlist ampliada para /renta-variable.
# IMPORTANTE: el upsert de _upsert_stock usa `{symbol}` como key — si un
# ticker está acá Y en HOME_STOCKS, el último que carga pisa al primero.
# YPF/GGAL/VIST viven en HOME_STOCKS grupo "Acciones". Big Tech idem.
# Los bloques "ADR Argentina" y "ADR LATAM" se sacaron 2026-05-20 — la mesa
# no los miraba en la watchlist del home.
EXTRA_STOCKS: list[tuple[str, str]] = []


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
        "timestamp":  datetime.fromtimestamp(q["t"], tz=UTC) if q.get("t") else now,
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


def ingesta(include_extra: bool = True) -> int:
    client = get_mongo_client()
    coll = client["Market"]["Quotes"]
    coll.create_index("symbol", unique=True)

    now = datetime.now(UTC)
    # HOME + EXTRA se pullean juntos (~38 tickers, dentro del cap Finnhub free).
    # El flag include_extra queda por compatibilidad pero el default es True.
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

    # US Treasury yields vía Yahoo
    for yahoo_sym, display in HOME_TREASURIES:
        try:
            q = yahoo_quote(yahoo_sym)
        except YahooError as e:
            logger.warning("treasury %s failed: %s", yahoo_sym, e)
            fail += 1
            continue
        if not q or q.get("c") is None:
            fail += 1
            continue
        # Guardamos con el display como symbol (no el ^ de Yahoo)
        last   = q.get("c")
        prev   = q.get("pc")
        pct_day = None
        if last is not None and prev:
            try:
                pct_day = (last - prev) / prev * 100
            except (TypeError, ZeroDivisionError):
                pass
        doc = {
            "symbol":     display,
            "yahoo_sym":  yahoo_sym,
            "type":       "treasury",
            "grupo":      "US Treasury",
            "last":       last,
            "prev_close": prev,
            "pct_day":    pct_day,
            "timestamp":  datetime.fromtimestamp(q["t"], tz=UTC) if q.get("t") else now,
            "updated_at": now,
        }
        coll.update_one({"symbol": display}, {"$set": doc}, upsert=True)
        ok += 1

    # Futuros CME/CBOT/COMEX/NYMEX vía Yahoo (continuous front-month).
    for yahoo_sym, display, exchange in HOME_FUTUROS:
        try:
            q = yahoo_quote(yahoo_sym)
        except YahooError as e:
            logger.warning("future %s failed: %s", yahoo_sym, e)
            fail += 1
            continue
        if not q or q.get("c") is None:
            fail += 1
            continue
        last = q.get("c")
        prev = q.get("pc")
        pct_day = None
        if last is not None and prev:
            try:
                pct_day = (last - prev) / prev * 100
            except (TypeError, ZeroDivisionError):
                pass
        doc = {
            "symbol":     display,
            "yahoo_sym":  yahoo_sym,
            "exchange":   exchange,
            "type":       "future",
            "grupo":      "Futuros",
            "last":       last,
            "prev_close": prev,
            "pct_day":    pct_day,
            "timestamp":  datetime.fromtimestamp(q["t"], tz=UTC) if q.get("t") else now,
            "updated_at": now,
        }
        coll.update_one({"symbol": display}, {"$set": doc}, upsert=True)
        ok += 1

    # Índices locales (MERVAL, etc) vía Yahoo
    for yahoo_sym, display, grupo in HOME_INDICES_YAHOO:
        try:
            q = yahoo_quote(yahoo_sym)
        except YahooError as e:
            logger.warning("index %s failed: %s", yahoo_sym, e)
            fail += 1
            continue
        if not q or q.get("c") is None:
            fail += 1
            continue
        last = q.get("c")
        prev = q.get("pc")
        pct_day = None
        if last is not None and prev:
            try:
                pct_day = (last - prev) / prev * 100
            except (TypeError, ZeroDivisionError):
                pass
        doc = {
            "symbol":     display,
            "yahoo_sym":  yahoo_sym,
            "type":       "index",
            "grupo":      grupo,
            "last":       last,
            "prev_close": prev,
            "pct_day":    pct_day,
            "timestamp":  datetime.fromtimestamp(q["t"], tz=UTC) if q.get("t") else now,
            "updated_at": now,
        }
        coll.update_one({"symbol": display}, {"$set": doc}, upsert=True)
        ok += 1

    # Limpieza: eliminar docs cuyo grupo ya no existe (ej. los ETFs viejos
    # de "Commodities" que migraron a "Futuros"). Idempotente.
    # Incluye SIEMPRE los grupos de EXTRA_STOCKS (ADR Argentina/LATAM)
    # aunque la corrida sea --no-extra: si no, la purga borra esos docs en
    # cada corrida sin --extra y la watchlist ADR queda vacía intermitente.
    grupos_validos = (
        {g for _, g in HOME_STOCKS}
        | {g for _, g in EXTRA_STOCKS}
        | {g for _, _, _, g in HOME_FX}
        | {g for _, _, g in HOME_INDICES_YAHOO}
        | {"Futuros", "US Treasury"}
    )
    purga = coll.delete_many({"grupo": {"$nin": list(grupos_validos)}})
    if purga.deleted_count:
        logger.info("market_quotes — purgados %d docs de grupos obsoletos", purga.deleted_count)

    logger.info(
        "market_quotes — ok=%d fail=%d stocks=%d fx=%d futuros=%d treasuries=%d indices=%d",
        ok, fail, len(stocks), len(HOME_FX), len(HOME_FUTUROS),
        len(HOME_TREASURIES), len(HOME_INDICES_YAHOO),
    )
    mirror_quotes_sql(coll)
    return 0 if fail < ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-extra", action="store_true",
                        help="Solo HOME_STOCKS (skip ADRs + Big Tech). Default: todos.")
    # Legacy: --extra ya no es necesario (default True), pero se acepta para compat con crons viejos.
    parser.add_argument("--extra", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ingesta(include_extra=not args.no_extra)


if __name__ == "__main__":
    sys.exit(main())
