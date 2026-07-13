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
from core.pg_mirror import doc_iso, prune_native, write_native

logger = logging.getLogger(__name__)

# Se borran los eventos ya pasados más viejos que esto (el calendario mira adelante).
RETENCION_DIAS = 2


def ingesta() -> int:
    try:
        data = economic_calendar()
    except FinnhubError as e:
        logger.error("economic_calendar failed: %s", e)
        return 2

    events = data.get("economicCalendar", []) or []
    logger.info("Finnhub devolvió %d eventos", len(events))

    # Solo interesan estos países y solo el ALTO impacto (nivel 3): CPI, tasas,
    # empleo, PBI, etc. El resto (feriados, países ajenos) es ruido que además
    # infla la tabla — se descarta en la ingesta.
    COUNTRIES = {"AR", "US", "BR"}
    IMPACT_MIN = 3

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
    # SQL-native: un único upsert por PK natural (evt_ts, country, event) — el mismo
    # unique index del escritor Mongo legacy. EconomicCalendar tiene UN solo writer
    # (este job) → no hay anchors ni $set parcial que preservar, write_native (que
    # reescribe el `data` jsonb) es suficiente. El `data` va con doc_iso (datetimes
    # ISO, igual que escribía el sync) para que market_sql lea byte-a-byte idéntico.
    pg_rows = []
    skip = 0
    for ev in events:
        raw_time = ev.get("time", "")
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            skip += 1
            continue

        country = ev.get("country", "")
        if country not in COUNTRIES:
            skip += 1
            continue
        impact = _impact_to_int(ev.get("impact"))
        if impact < IMPACT_MIN:
            skip += 1
            continue

        event = ev.get("event", "")
        doc = {
            "time":     dt,
            "country":  country,
            "event":    event,
            "impact":   impact,
            "actual":   ev.get("actual"),
            "prev":     ev.get("prev"),
            "estimate": ev.get("estimate"),
            "unit":     ev.get("unit", ""),
            "fetched_at": now,
        }
        pg_rows.append({
            "evt_ts": dt, "country": country, "event": event,
            "impact": doc["impact"], "data": doc_iso(doc),
        })

    n = write_native("market_calendar", ["evt_ts", "country", "event"], pg_rows)
    prune_native("market_calendar", "evt_ts", RETENCION_DIAS)  # TTL: fuera lo pasado viejo
    logger.info("economic_calendar — escritos=%d skip=%d total=%d", n, skip, len(events))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
