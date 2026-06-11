"""api/services/market_sql.py — Market (watchlist + calendario económico) leyendo Postgres.

Espejo de los endpoints read-only de api/routers/market.py (`/quotes` y
`/calendar/economic`). Mismo shape que el path Mongo: el doc completo viaja en
`data jsonb` (fechas ya ISO, sin `_id`) y los retornos se computan on-the-fly
desde los anchors con la MISMA regla (`compute_returns`, que el router también
usa en su path Mongo para que no haya drift). Dual-run por flag `MARKET_SQL`.

`/candle` y `/profile` son APIs externas (Yahoo/Finnhub) — no tocan Mongo, no migran.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def compute_returns(d: dict) -> dict:
    """Retornos on-the-fly desde anchors. ÚNICA implementación de la regla
    (el `_serialize` del router Mongo la invoca también)."""
    last = d.get("last")
    for key_ret, key_anchor in [
        ("ret_7d",  "anchor_7d"),
        ("ret_mtd", "anchor_mtd"),
        ("ret_ytd", "anchor_ytd"),
        ("ret_1y",  "anchor_1y"),
    ]:
        anchor = d.get(key_anchor)
        if last is not None and anchor:
            try:
                d[key_ret] = round((last - anchor) / anchor * 100, 2)
            except (TypeError, ZeroDivisionError):
                d[key_ret] = None
        else:
            d[key_ret] = None
    return d


def quotes(symbols: list[str] | None = None) -> list[dict]:
    """Últimas cotizaciones del watchlist. `symbols` ya viene upper/strip del router."""
    if symbols:
        rows = _q("SELECT data FROM market_quotes WHERE symbol = ANY(%(s)s)", {"s": symbols})
    else:
        rows = _q("SELECT data FROM market_quotes")
    docs = [compute_returns(dict(r["data"])) for r in rows]
    docs.sort(key=lambda d: (d.get("grupo", "ZZZ"), d.get("symbol", "")))
    return docs


def calendar_economic(desde: datetime, hasta: datetime, importancia: int = 0,
                      country: str | None = None, limit: int = 500) -> list[dict]:
    """Eventos macro en [desde, hasta]. Filtra por `evt_ts` (timestamptz materializado:
    NULL cuando `time` no era datetime en Mongo → esos docs tampoco matchean el rango
    en el path Mongo, misma semántica)."""
    conds = ["evt_ts >= %(desde)s", "evt_ts <= %(hasta)s"]
    p: dict = {"desde": desde, "hasta": hasta, "limit": int(limit)}
    if importancia:
        conds.append("impact >= %(imp)s")
        p["imp"] = int(importancia)
    if country:
        conds.append("country = %(country)s")
        p["country"] = country.upper()
    rows = _q(f"SELECT data FROM market_calendar WHERE {' AND '.join(conds)} "
              f"ORDER BY evt_ts ASC LIMIT %(limit)s", p)
    return [compute_returns(dict(r["data"])) for r in rows]
