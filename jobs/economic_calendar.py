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
from datetime import UTC, datetime

from core.finnhub import FinnhubError, economic_calendar
from core.mongo import get_mongo_client
from core.pg_mirror import doc_iso, jobs_on, mirror_job

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

    # Finnhub puede devolver impact como string ('low'/'medium'/'high') o int (1-3).
    # Normalizamos todo a int: 0=none, 1=low, 2=medium, 3=high.
    IMPACT_MAP = {"low": 1, "medium": 2, "high": 3}

    def _impact_to_int(v) -> int:
        if v is None:
            return 0
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            return IMPACT_MAP.get(v.strip().lower(), 0)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    now = datetime.now(UTC)
    ins = upd = skip = 0
    for ev in events:
        raw_time = ev.get("time", "")
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            skip += 1
            continue

        key = {"time": dt, "country": ev.get("country", ""), "event": ev.get("event", "")}
        doc = {
            **key,
            "impact":   _impact_to_int(ev.get("impact")),
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

    # Dual-write a Postgres (flag MERCADO_SQL_WRITE, best-effort). Se RE-LEE la
    # colección (en vez de espejar los docs en memoria): los datetimes vuelven de
    # Mongo naive y truncados a ms — idéntico a lo que ve el sync y sirve la API.
    if jobs_on():
        pg_rows = []
        for d in coll.find({}, {"_id": 0}):
            evt = d.get("time")
            if not isinstance(evt, datetime):
                continue
            pg_rows.append({
                "evt_ts": evt.replace(tzinfo=UTC) if evt.tzinfo is None else evt,
                "country": d.get("country") or "", "event": d.get("event") or "",
                "impact": int(d.get("impact") or 0), "data": doc_iso(d),
            })
        mirror_job("market_calendar", ["evt_ts", "country", "event"], pg_rows)

    logger.info("economic_calendar — ins=%d upd=%d skip=%d total=%d", ins, upd, skip, len(events))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
