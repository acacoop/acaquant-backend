"""calendario.py — calendario económico para la watchlist HOME (tab CALENDARIO).

Lee home.market_calendar (lo llena jobs/economic_calendar vía FMP) — ya filtrado a
AR/US/BR de alto impacto. Devuelve los próximos eventos ordenados por fecha.
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool


@cached(ttl=300)
def get_calendario(dias: int = 45, limit: int = 80) -> list[dict[str, Any]]:
    """Próximos eventos del calendario: de hace 12hs (para ver lo de hoy que ya
    salió) hasta `dias` adelante, ordenados asc por fecha."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT evt_ts, country, event, impact, data
            FROM home.market_calendar
            WHERE evt_ts >= now() - interval '12 hours'
              AND evt_ts <= now() + make_interval(days => %s)
            ORDER BY evt_ts ASC
            LIMIT %s
            """,
            (dias, limit),
        )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = r["data"] or {}
        out.append({
            "evt_ts":   r["evt_ts"].isoformat() if r["evt_ts"] else None,
            "country":  r["country"],
            "event":    r["event"],
            "impact":   r["impact"],
            "actual":   d.get("actual"),
            "prev":     d.get("prev"),
            "estimate": d.get("estimate"),
            "unit":     d.get("unit"),
        })
    return out
