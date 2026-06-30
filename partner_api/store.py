"""partner_api/store.py — capa de datos SQL-native (decomiso Mongo).

Lee Postgres `partner.cartera` / `partner.api_users` (poblado por
`jobs/partner_export.py` y `scripts/partner_user.py`). ACAPortfolio (Mongo) fue
eliminada — el flag PARTNER_SQL y el path Mongo ya no existen.

`fecha` se expone SIEMPRE como string 'YYYY-MM-DD' (en SQL es `date` → ISO).
`cantidad/precio/valuacion` como float; `exported_at` NO se devuelve (interno).
Las consumen `auth.py` (login + guard), `routes.py` (REST /v1/*) y `odata.py`.
"""
from __future__ import annotations

from datetime import date

from partner_api import pg


# ── helpers de tipado SQL → shape de salida ──────────────────────────────────
def _f(x):
    return float(x) if x is not None else None


def _fecha_str(v) -> str | None:
    """date (SQL) → 'YYYY-MM-DD'. None → None."""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _row(d: dict) -> dict:
    """Una posición en el shape público."""
    return {
        "fecha":     _fecha_str(d.get("fecha")),
        "id_cuenta": d.get("id_cuenta"),
        "cuenta":    d.get("cuenta"),
        "unidad":    d.get("unidad"),
        "cantidad":  _f(d.get("cantidad")),
        "precio":    _f(d.get("precio")),
        "valuacion": _f(d.get("valuacion")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# USUARIOS (ApiUsers) — login + guard + Basic auth de OData
# ─────────────────────────────────────────────────────────────────────────────
def find_user(username: str) -> dict | None:
    """Devuelve {username, password_hash, enabled} o None."""
    pg.ensure_schema()
    with pg.get_pool().connection() as conn:
        cur = conn.execute(
            "SELECT username, password_hash, enabled "
            "FROM partner.api_users WHERE username = %s",
            (username,),
        )
        r = cur.fetchone()
    if not r:
        return None
    return {"username": r[0], "password_hash": r[1], "enabled": bool(r[2])}


# ─────────────────────────────────────────────────────────────────────────────
# CARTERA — fechas + portfolio (REST /v1/*)
# ─────────────────────────────────────────────────────────────────────────────
def distinct_fechas() -> list[str]:
    """Fechas disponibles, de la más reciente a la más vieja (sin vacías)."""
    pg.ensure_schema()
    with pg.get_pool().connection() as conn:
        cur = conn.execute(
            "SELECT DISTINCT fecha FROM partner.cartera "
            "WHERE fecha IS NOT NULL ORDER BY fecha DESC"
        )
        return [r[0].isoformat() for r in cur.fetchall()]


def latest_fecha() -> str | None:
    """Fecha más reciente con datos, o None si la cartera está vacía."""
    pg.ensure_schema()
    with pg.get_pool().connection() as conn:
        cur = conn.execute("SELECT max(fecha) FROM partner.cartera")
        r = cur.fetchone()
    return r[0].isoformat() if r and r[0] is not None else None


def portfolio(fecha: str, id_cuenta: str | None = None) -> list[dict]:
    """Posiciones de `fecha` (opcionalmente filtradas por `id_cuenta`), ordenadas
    por (id_cuenta, unidad)."""
    pg.ensure_schema()
    sql = (
        "SELECT fecha, id_cuenta, cuenta, unidad, cantidad, precio, valuacion "
        "FROM partner.cartera WHERE fecha = %(fecha)s"
    )
    params: dict = {"fecha": fecha}
    if id_cuenta:
        sql += " AND id_cuenta = %(id_cuenta)s"
        params["id_cuenta"] = str(id_cuenta).strip()
    sql += " ORDER BY id_cuenta, unidad"
    from psycopg.rows import dict_row
    with pg.get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [_row(d) for d in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# CARTERA — OData (servicio v2). El filtro llega ya parseado a dict
# ({campo: valor} igualdad) por odata._parse_filter → se traduce a WHERE en SQL.
# ─────────────────────────────────────────────────────────────────────────────
_ODATA_COLS = ("fecha", "id_cuenta", "cuenta", "unidad")  # campos filtrables


def _odata_where(flt: dict) -> tuple[str, dict]:
    conds, params = [], {}
    for k, v in flt.items():
        if k not in _ODATA_COLS:
            continue
        params[k] = v
        # `fecha` es date en SQL pero llega como 'YYYY-MM-DD' string del $filter.
        conds.append(f"{k} = %({k})s::date" if k == "fecha" else f"{k} = %({k})s")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def odata_query(flt: dict, *, skip: int | None, top: int | None, max_rows: int) -> list[dict]:
    """Posiciones para el entity set OData."""
    pg.ensure_schema()
    where, params = _odata_where(flt)
    sql = (
        "SELECT fecha, id_cuenta, cuenta, unidad, cantidad, precio, valuacion "
        "FROM partner.cartera" + where +
        " ORDER BY fecha DESC, id_cuenta, unidad"
    )
    lim = min(top, max_rows) if top else max_rows
    sql += " LIMIT %(_lim)s"
    params["_lim"] = lim
    if skip:
        sql += " OFFSET %(_off)s"
        params["_off"] = skip
    from psycopg.rows import dict_row
    with pg.get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [_row(d) for d in cur.fetchall()]


def odata_count(flt: dict) -> int:
    """Conteo de filas que matchean el $filter."""
    pg.ensure_schema()
    where, params = _odata_where(flt)
    with pg.get_pool().connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM partner.cartera" + where, params)
        return int(cur.fetchone()[0])
