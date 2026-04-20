"""market_anchors.py — anchors diarios de retorno (7d, MTD, YTD, 1Y).

Corre 1×/día post-cierre US (22:00 UTC = 19:00 ART). Para cada símbolo fetchea
candle diario de ~13 meses, calcula el cierre más cercano a cada anchor
(7 días atrás, primer día del mes, primer día del año, 365 días atrás) y lo
guarda en el mismo doc de Market.Quotes.

Luego la API, al leer las quotes, computa los retornos on-the-fly:
    ret_7d = (last - anchor_7d) / anchor_7d * 100

FX: se computa con frankfurter.app (histórico por fecha).

Cron sugerido:
    0 22 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.market_anchors
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

import requests

from core.mongo import get_mongo_client
from core.yahoo import YahooError, stock_candle
from jobs.market_quotes import EXTRA_STOCKS, HOME_FX, HOME_STOCKS

logger = logging.getLogger(__name__)

FRANKFURTER_DATE = "https://api.frankfurter.app"


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
    return {
        "anchor_7d":  int((now - timedelta(days=7)).timestamp()),
        "anchor_mtd": int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()),
        "anchor_ytd": int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp()),
        "anchor_1y":  int((now - timedelta(days=365)).timestamp()),
    }


def update_stock_anchors(coll, sym: str, now: datetime) -> bool:
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

    coll.update_one({"symbol": sym}, {"$set": update}, upsert=True)
    return True


def _frankfurter_hist(base: str, target: str, date: str) -> float | None:
    try:
        r = requests.get(f"{FRANKFURTER_DATE}/{date}",
                         params={"from": base, "to": target}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float((data.get("rates") or {}).get(target))
    except Exception as e:
        logger.warning("frankfurter hist %s→%s %s failed: %s", base, target, date, e)
        return None


def update_fx_anchors(coll, display: str, base: str, target: str, now: datetime) -> bool:
    """Para FX: 1 request por anchor. frankfurter cachea internamente ECB
    reference rates, así que es rápido y gratis."""
    date_7d  = (now - timedelta(days=7)).date().isoformat()
    date_mtd = datetime(now.year, now.month, 1, tzinfo=timezone.utc).date().isoformat()
    date_ytd = datetime(now.year, 1, 1, tzinfo=timezone.utc).date().isoformat()
    date_1y  = (now - timedelta(days=365)).date().isoformat()

    update = {
        "anchor_7d":  _frankfurter_hist(base, target, date_7d),
        "anchor_mtd": _frankfurter_hist(base, target, date_mtd),
        "anchor_ytd": _frankfurter_hist(base, target, date_ytd),
        "anchor_1y":  _frankfurter_hist(base, target, date_1y),
        "anchors_updated_at": now,
    }
    coll.update_one({"symbol": display}, {"$set": update}, upsert=True)
    return any(update[k] is not None for k in ("anchor_7d", "anchor_mtd", "anchor_ytd", "anchor_1y"))


def ingesta() -> int:
    client = get_mongo_client()
    coll = client["Market"]["Quotes"]
    now = datetime.now(timezone.utc)

    all_stocks = [sym for sym, _ in HOME_STOCKS + EXTRA_STOCKS]
    ok_s = fail_s = 0
    for sym in all_stocks:
        if update_stock_anchors(coll, sym, now):
            ok_s += 1
        else:
            fail_s += 1

    ok_fx = fail_fx = 0
    for display, base, target, _grupo in HOME_FX:
        if update_fx_anchors(coll, display, base, target, now):
            ok_fx += 1
        else:
            fail_fx += 1

    logger.info("market_anchors — stocks ok=%d fail=%d · fx ok=%d fail=%d",
                ok_s, fail_s, ok_fx, fail_fx)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
