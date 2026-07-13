"""economic_calendar.py — ingesta diaria del calendario económico (FMP).

FMP (financialmodelingprep) reemplaza a Finnhub (free dejó de traer datos ~2026).
Se piden 60 días forward y se guarda SOLO AR/US/BR de ALTO impacto. Upsert
idempotente por (evt_ts, country, event) en home.market_calendar.

Cron: 1 vez por día a las 06:00 UTC.
    0 6 * * 1-5 cd /root/TradingAV && venv/bin/python -m jobs.economic_calendar
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta

from core.fmp import FmpError, economic_calendar
from core.pg_mirror import doc_iso, prune_native, write_native

logger = logging.getLogger(__name__)

# Se borran los eventos ya pasados más viejos que esto (el calendario mira adelante).
RETENCION_DIAS = 2
DIAS_FORWARD = 60

# Solo estos países. FMP suele mandar el código ISO2 ('US','AR','BR'), pero por si
# manda el nombre completo, mapeamos. Solo ALTO impacto (nivel 3).
COUNTRIES = {"AR", "US", "BR"}
COUNTRY_ALIASES = {
    "argentina": "AR", "united states": "US", "usa": "US",
    "brazil": "BR", "brasil": "BR",
}
IMPACT_MIN = 3
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


def _country_code(v: str) -> str | None:
    c = (v or "").strip()
    if c.upper() in COUNTRIES:
        return c.upper()
    return COUNTRY_ALIASES.get(c.lower())


def ingesta() -> int:
    hoy = datetime.now(UTC).date()
    try:
        events = economic_calendar(
            desde=hoy.isoformat(), hasta=(hoy + timedelta(days=DIAS_FORWARD)).isoformat())
    except FmpError as e:
        logger.error("economic_calendar (FMP) falló: %s", e)
        return 2

    logger.info("FMP devolvió %d eventos", len(events))
    now = datetime.now(UTC)
    pg_rows = []
    skip = 0
    for ev in events:
        raw_time = ev.get("date", "")
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            skip += 1
            continue

        country = _country_code(ev.get("country", ""))
        if country is None:
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
            "prev":     ev.get("previous"),
            "estimate": ev.get("estimate"),
            "unit":     ev.get("unit", ""),
            "currency": ev.get("currency"),
            "fetched_at": now,
        }
        pg_rows.append({
            "evt_ts": dt, "country": country, "event": event,
            "impact": impact, "data": doc_iso(doc),
        })

    n = write_native("market_calendar", ["evt_ts", "country", "event"], pg_rows)
    prune_native("market_calendar", "evt_ts", RETENCION_DIAS)  # TTL: fuera lo pasado viejo
    logger.info("economic_calendar (FMP) — escritos=%d skip=%d total=%d", n, skip, len(events))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(ingesta())
