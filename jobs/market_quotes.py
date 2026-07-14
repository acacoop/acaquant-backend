"""market_quotes.py — cotizaciones de equity/futuros/índices para el watchlist HOME.

Patrón eficiente: UN poller centralizado llena `home.market_quotes` con el último
snapshot por símbolo. Los clientes (home, /renta-variable, briefing) leen esa tabla
— cero hammering adicional sobre el proveedor aunque haya muchos tabs abiertos.

Fuentes:
- Equities / ETFs de índice: Finnhub /quote.
- Futuros (CME/CBOT/COMEX/NYMEX/ICE), cripto spot, treasuries e índices locales
  (MERVAL): Yahoo. Finnhub free no cotiza ninguno de esos.

El bloque de FOREX (frankfurter.app) se eliminó junto con las monedas del
watchlist: la mesa las sacó y la lista quedó vacía.

Cron (cada 1 min en horario de mercado, ver deploy/crontab.txt):
    * 12-23 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_quotes
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

from core.finnhub import FinnhubError, quote
from core.pg_mirror import merge_jsonb_native
from core.yahoo import YahooError, yahoo_quote

logger = logging.getLogger(__name__)


def _write_quote(doc: dict) -> None:
    """Persiste UN símbolo SQL-native vía MERGE atómico (`data = data || patch`).
    Crítico que sea merge y no overwrite: los anchors (anchor_7d/mtd/ytd/1y, que
    escribe jobs.market_anchors sobre el MISMO doc) viven dentro del `data` jsonb
    — un write_native que pisara `data` entero los borraría (incidente que revirtió
    el intento previo). Cada símbolo mergea SOLO sus campos de precio/intraday."""
    sym = doc.get("symbol")
    if not sym:
        return
    merge_jsonb_native("market_quotes", ["symbol"], [sym], doc)

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
    ("SPCX", "Índices"),
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

def _upsert_stock(sym: str, grupo: str, q: dict, now: datetime) -> bool:
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
    _write_quote(doc)
    return True


def ingesta() -> int:
    now = datetime.now(UTC)

    ok = fail = 0
    for sym, grupo in HOME_STOCKS:
        try:
            q = quote(sym)
        except FinnhubError as e:
            logger.warning("quote %s failed: %s", sym, e)
            fail += 1
            continue
        if _upsert_stock(sym, grupo, q, now):
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
        _write_quote(doc)
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
        _write_quote(doc)
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
        _write_quote(doc)
        ok += 1

    # Limpieza: eliminar filas cuyo grupo ya no existe (ej. los ETFs viejos de
    # "Commodities" que migraron a "Futuros"). Idempotente. Filtra por
    # `data->>'grupo'` (el grupo vive dentro del jsonb; la columna `grupo` queda
    # NULL con el merge y no se lee).
    grupos_validos = (
        {g for _, g in HOME_STOCKS}
        | {g for _, _, g in HOME_INDICES_YAHOO}
        | {"Futuros", "US Treasury"}
    )
    _purga_grupos_obsoletos(grupos_validos)

    logger.info(
        "market_quotes — ok=%d fail=%d stocks=%d futuros=%d treasuries=%d indices=%d",
        ok, fail, len(HOME_STOCKS), len(HOME_FUTUROS),
        len(HOME_TREASURIES), len(HOME_INDICES_YAHOO),
    )
    return 0 if fail < ok else 1


def _purga_grupos_obsoletos(grupos_validos: set[str]) -> None:
    """Borra filas de market_quotes cuyo `data->>'grupo'` ya no es válido.
    Best-effort: un fallo de SQL nunca tumba el job (la watchlist sigue fresca)."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM market_quotes "
                "WHERE COALESCE(data->>'grupo', '') <> ALL(%s)",
                (list(grupos_validos),),
            )
            if cur.rowcount:
                logger.info("market_quotes — purgadas %d filas de grupos obsoletos",
                            cur.rowcount)
    except Exception as e:
        logger.error("market_quotes purga: %s", str(e).splitlines()[0][:200])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ingesta()


if __name__ == "__main__":
    sys.exit(main())
