"""market_anchors.py — anchors diarios de retorno (7d, MTD, YTD, 1Y).

Corre 1×/día post-cierre US (22:00 UTC = 19:00 ART). Para cada símbolo fetchea
candle diario de ~13 meses, calcula el cierre más cercano a cada anchor
(7 días atrás, primer día del mes, primer día del año, 365 días atrás) y lo
guarda en el mismo doc de Market.Quotes.

Luego la API, al leer las quotes, computa los retornos on-the-fly:
    ret_7d = (last - anchor_7d) / anchor_7d * 100

El bloque de anchors de FX (frankfurter.app) se eliminó junto con las monedas del
watchlist: la mesa las sacó de la vista y la lista de símbolos quedó vacía.

Cron:
    0 22 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_anchors
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta

from core.pg_mirror import merge_jsonb_native
from core.yahoo import YahooError, stock_candle
from jobs.market_quotes import (
    HOME_FUTUROS,
    HOME_INDICES_YAHOO,
    HOME_STOCKS,
    HOME_TREASURIES,
)

logger = logging.getLogger(__name__)


def _closest_close(times: list[int], closes: list[float], target_ts: int) -> float | None:
    """Cierre más cercano (<=) al target_ts. O null si no hay data previa."""
    last = None
    for t, c in zip(times, closes):
        if t <= target_ts:
            last = c
        else:
            break
    return last


def _anchor_timestamps(now: datetime) -> dict[str, int]:
    # WTD = week-to-date: cierre de la última rueda ANTES del lunes de esta semana
    # (típicamente el viernes). Distinto de anchor_7d (rolling 7 días), que se
    # mantiene para /argy. _closest_close toma el cierre <= al target.
    inicio_semana = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=now.weekday())
    return {
        "anchor_7d":  int((now - timedelta(days=7)).timestamp()),
        "anchor_wtd": int((inicio_semana - timedelta(seconds=1)).timestamp()),
        "anchor_mtd": int(datetime(now.year, now.month, 1, tzinfo=UTC).timestamp()),
        "anchor_ytd": int(datetime(now.year, 1, 1, tzinfo=UTC).timestamp()),
        "anchor_1y":  int((now - timedelta(days=365)).timestamp()),
    }


def update_stock_anchors(sym: str, now: datetime) -> bool:
    hasta = int(now.timestamp())
    desde = hasta - 400 * 86400  # ~13 meses de colchón
    try:
        c = stock_candle(sym, "D", desde, hasta)
    except YahooError as e:
        logger.warning("candle %s failed: %s", sym, e)
        return False
    if c.get("s") != "ok":
        logger.warning("candle %s status=%s", sym, c.get("s"))
        return False

    times  = c.get("t")  or []
    closes = c.get("c")  or []
    if not times or not closes:
        return False

    ts_map = _anchor_timestamps(now)
    update = {k: _closest_close(times, closes, ts) for k, ts in ts_map.items()}
    update["anchors_updated_at"] = now

    # MERGE atómico (`data = data || patch`): solo toca los anchors, preserva el
    # precio/intraday que escribe jobs.market_quotes en el MISMO doc jsonb.
    merge_jsonb_native("market_quotes", ["symbol"], [sym], update)
    return True


def ingesta() -> int:
    now = datetime.now(UTC)

    ok_s = fail_s = 0
    for sym, _grupo in HOME_STOCKS:
        if update_stock_anchors(sym, now):
            ok_s += 1
        else:
            fail_s += 1

    # Treasury yields: el yahoo_sym es lo que fetchamos, pero el doc se
    # guarda bajo el display (UST 10Y, etc). Necesitamos update_stock_anchors
    # usando yahoo_sym y luego guardar bajo display.
    ok_t = fail_t = 0
    for yahoo_sym, display in HOME_TREASURIES:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_t += 1
        else:
            fail_t += 1

    # Índices locales (MERVAL, etc) — mismo patrón display ≠ yahoo_sym.
    ok_i = fail_i = 0
    for yahoo_sym, display, _grupo in HOME_INDICES_YAHOO:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_i += 1
        else:
            fail_i += 1

    # Futuros (S&P/Nasdaq/WTI/Brent/Oro/Soja/Maíz/Trigo/BTC/ETH) — mismo patrón
    # display ≠ yahoo_sym (se fetchea por ES=F, se guarda por 'S&P FUT'). Sin esto
    # los futuros del watchlist/briefing quedan sin retorno semana/mes.
    ok_f = fail_f = 0
    for yahoo_sym, display, _exchange in HOME_FUTUROS:
        if _update_treasury_anchors(yahoo_sym, display, now):
            ok_f += 1
        else:
            fail_f += 1

    logger.info(
        "market_anchors — stocks ok=%d fail=%d · treasuries ok=%d fail=%d · "
        "indices ok=%d fail=%d · futuros ok=%d fail=%d",
        ok_s, fail_s, ok_t, fail_t, ok_i, fail_i, ok_f, fail_f,
    )
    return 0


def _update_treasury_anchors(yahoo_sym: str, display: str, now: datetime) -> bool:
    """Variante de update_stock_anchors que guarda bajo display pero fetchea
    con el yahoo_sym (^IRX, ^TNX, etc)."""
    hasta = int(now.timestamp())
    desde = hasta - 400 * 86400
    try:
        c = stock_candle(yahoo_sym, "D", desde, hasta)
    except YahooError as e:
        logger.warning("treasury candle %s failed: %s", yahoo_sym, e)
        return False
    if c.get("s") != "ok":
        return False
    times  = c.get("t")  or []
    closes = c.get("c")  or []
    if not times or not closes:
        return False
    ts_map = _anchor_timestamps(now)
    update = {k: _closest_close(times, closes, ts) for k, ts in ts_map.items()}
    update["anchors_updated_at"] = now
    merge_jsonb_native("market_quotes", ["symbol"], [display], update)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
