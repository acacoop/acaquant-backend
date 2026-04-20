"""economic_calendar.py — ingesta diaria del calendario económico global.

Finnhub devuelve ~60 días forward de eventos macro (FOMC, CPI, jobs, etc)
en un solo endpoint sin parámetros. Upsert idempotente por (time, country,
event).

Cron: 1 vez por día a las 06:00 UTC.
    0 6 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.economic_calendar
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from core.finnhub import FinnhubError, economic_calendar
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)


def ingesta() -> int:
    client = get_mongo_client()
    coll = client["Market"]["EconomicCalendar"]
    coll.create_index([("time", 1), ("country", 1), ("event", 1)], unique=True)
    coll.create_index("time")
    coll.create_index([("impact", 1), ("time", 1)])

    try:
        data = economic_calendar()
    except FinnhubError as e:
        logger.error("economic_calendar failed: %s", e)
        return 2

    events = data.get("economicCalendar", []) or []
    logger.info("Finnhub devolvió %d eventos", len(events))

    now = datetime.now(timezone.utc)
    ins = upd = skip = 0
    for ev in events:
        raw_time = ev.get("time", "")
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            skip += 1
            continue

        key = {"time": dt, "country": ev.get("country", ""), "event": ev.get("event", "")}
        doc = {
            **key,
            "impact":   int(ev.get("impact", 0) or 0),  # 0 low … 3 high
            "actual":   ev.get("actual"),
            "prev":     ev.get("prev"),
            "estimate": ev.get("estimate"),
            "unit":     ev.get("unit", ""),
            "fetched_at": now,
        }
        result = coll.update_one(key, {"$set": doc}, upsert=True)
        if result.upserted_id is not None:
            ins += 1
        elif result.modified_count:
            upd += 1

    logger.info("economic_calendar — ins=%d upd=%d skip=%d total=%d", ins, upd, skip, len(events))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
