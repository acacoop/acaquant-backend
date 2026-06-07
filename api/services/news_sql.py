"""api/services/news_sql.py — News (home) leyendo de Postgres. Espejo de los endpoints
read-only de api/routers/news.py (list_headlines + stats). Mismo shape (devuelve el doc tal
cual, fechas ISO, vía data jsonb). Dual-run por flag NEWS_SQL.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def list_headlines(desde=None, hasta=None, fuente=None, categoria=None, keyword=None,
                   limit=100, skip=0) -> list:
    conds = ["TRUE"]
    p: dict = {"limit": int(limit), "skip": int(skip)}
    if desde:
        conds.append("fecha_publicacion >= %(desde)s")
        p["desde"] = desde
    if hasta:
        conds.append("fecha_publicacion <= %(hasta)s")
        p["hasta"] = hasta
    if fuente:
        conds.append("fuente = %(fuente)s")
        p["fuente"] = fuente
    if categoria:
        conds.append("categoria = %(cat)s")
        p["cat"] = categoria
    if keyword:
        conds.append("titulo ILIKE %(kw)s")
        p["kw"] = "%" + keyword.replace("%", r"\%").replace("_", r"\_") + "%"
    rows = _q(f"SELECT data FROM news_headlines WHERE {' AND '.join(conds)} "
              f"ORDER BY fecha_publicacion DESC NULLS LAST OFFSET %(skip)s LIMIT %(limit)s", p)
    return [r["data"] for r in rows]


def stats(horas: int = 24) -> dict:
    desde = datetime.now(UTC) - timedelta(hours=horas)
    rows = _q("SELECT fuente, count(*) AS count, max(fecha_publicacion) AS last "
              "FROM news_headlines WHERE fecha_publicacion >= %(d)s "
              "GROUP BY fuente ORDER BY count DESC", {"d": desde})
    out = [{"fuente": r["fuente"], "count": r["count"],
            "last": r["last"].astimezone(UTC).isoformat() if r["last"] else None}
           for r in rows]
    return {"periodo_horas": horas, "total": sum(r["count"] for r in out), "por_fuente": out}
