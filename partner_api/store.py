"""partner_api/store.py — capa de datos con dual-run Mongo/SQL.

Selector por env var `PARTNER_SQL`:
  * PARTNER_SQL ausente / '0' (DEFAULT) → lee Mongo `ACAPortfolio` (path original
    INTACTO). Rollback = sacar la env + restart del servicio.
  * PARTNER_SQL='1' → lee Postgres `partner.cartera` / `partner.api_users`.

Las funciones devuelven EXACTAMENTE el mismo shape en los dos backends, para que
el proveedor externo no perciba ningún cambio de comportamiento al togglear el
flag. Las consumen `auth.py` (login + guard), `routes.py` (REST /v1/*) y
`odata.py` (servicio OData v2).

`fecha` se expone SIEMPRE como string 'YYYY-MM-DD' (en Mongo es string; en SQL es
`date` → se formatea a ISO). `cantidad/precio/valuacion` como float; `exported_at`
NO se devuelve (campo interno, oculto en los dos paths, igual que hoy).
"""
from __future__ import annotations

import os
from datetime import date


def _sql_enabled() -> bool:
    """Lee el flag en CADA llamada (no se cachea a import-time) → togglear la env
    + restart alcanza, sin tener que tocar código."""
    return os.getenv("PARTNER_SQL", "").strip() in ("1", "true", "True")


# ── helpers de tipado SQL → shape de salida ──────────────────────────────────
def _f(x):
    return float(x) if x is not None else None


def _fecha_str(v) -> str | None:
    """date (SQL) | str (Mongo) → 'YYYY-MM-DD'. None → None."""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _row(d: dict) -> dict:
    """Una posición en el shape público (idéntico al doc Mongo proyectado)."""
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
    """Devuelve {username, password_hash, enabled} o None. Mismo contrato en los
    dos backends (los callers solo usan esos 3 campos)."""
    if _sql_enabled():
        from partner_api import pg
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

    from partner_api.db import get_db
    return get_db()["ApiUsers"].find_one({"username": username})


# ─────────────────────────────────────────────────────────────────────────────
# CARTERA — fechas + portfolio (REST /v1/*)
# ─────────────────────────────────────────────────────────────────────────────
def distinct_fechas() -> list[str]:
    """Fechas disponibles, de la más reciente a la más vieja (sin vacías)."""
    if _sql_enabled():
        from partner_api import pg
        pg.ensure_schema()
        with pg.get_pool().connection() as conn:
            cur = conn.execute(
                "SELECT DISTINCT fecha FROM partner.cartera "
                "WHERE fecha IS NOT NULL ORDER BY fecha DESC"
            )
            return [r[0].isoformat() for r in cur.fetchall()]

    from partner_api.db import get_db
    col = get_db()["Cartera"]
    return sorted((f for f in col.distinct("fecha") if f), reverse=True)


def latest_fecha() -> str | None:
    """Fecha más reciente con datos, o None si la cartera está vacía."""
    if _sql_enabled():
        from partner_api import pg
        pg.ensure_schema()
        with pg.get_pool().connection() as conn:
            cur = conn.execute("SELECT max(fecha) FROM partner.cartera")
            r = cur.fetchone()
        return r[0].isoformat() if r and r[0] is not None else None

    from partner_api.db import get_db
    last = get_db()["Cartera"].find_one({}, {"_id": 0, "fecha": 1}, sort=[("fecha", -1)])
    return last["fecha"] if last else None


def portfolio(fecha: str, id_cuenta: str | None = None) -> list[dict]:
    """Posiciones de `fecha` (opcionalmente filtradas por `id_cuenta`), ordenadas
    por (id_cuenta, unidad) — igual que el path Mongo."""
    if _sql_enabled():
        from partner_api import pg
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

    from partner_api.db import get_db
    filtro: dict = {"fecha": fecha}
    if id_cuenta:
        filtro["id_cuenta"] = str(id_cuenta).strip()
    hide = {"_id": 0, "exported_at": 0}
    docs = list(get_db()["Cartera"].find(filtro, hide).sort(
        [("id_cuenta", 1), ("unidad", 1)]))
    return [_row(d) for d in docs]


# ─────────────────────────────────────────────────────────────────────────────
# CARTERA — OData (servicio v2). El filtro llega ya parseado a dict estilo Mongo
# ({campo: valor} igualdad) por odata._parse_filter → se traduce a WHERE en SQL.
# ─────────────────────────────────────────────────────────────────────────────
_ODATA_COLS = ("fecha", "id_cuenta", "cuenta", "unidad")  # campos filtrables


def _odata_where(flt: dict) -> tuple[str, dict]:
    conds, params = [], {}
    for k, v in flt.items():
        if k not in _ODATA_COLS:
            continue
        params[k] = v
        # `fecha` es date en SQL pero llega como 'YYYY-MM-DD' string del $filter →
        # el cast lo resuelve el driver comparando text vs date; lo forzamos explícito.
        conds.append(f"{k} = %({k})s::date" if k == "fecha" else f"{k} = %({k})s")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def odata_query(flt: dict, *, skip: int | None, top: int | None, max_rows: int) -> list[dict]:
    """Posiciones para el entity set OData. Mismo orden y proyección que el path
    Mongo (`odata._query`)."""
    if _sql_enabled():
        from partner_api import pg
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

    from partner_api.db import get_db
    cur = get_db()["Cartera"].find(flt, {"_id": 0, "exported_at": 0}).sort(
        [("fecha", -1), ("id_cuenta", 1), ("unidad", 1)])
    if skip:
        cur = cur.skip(skip)
    lim = min(top, max_rows) if top else max_rows
    cur = cur.limit(lim)
    return [_row(d) for d in cur]


def odata_count(flt: dict) -> int:
    """Conteo de filas que matchean el $filter."""
    if _sql_enabled():
        from partner_api import pg
        pg.ensure_schema()
        where, params = _odata_where(flt)
        with pg.get_pool().connection() as conn:
            cur = conn.execute(
                "SELECT count(*) FROM partner.cartera" + where, params)
            return int(cur.fetchone()[0])

    from partner_api.db import get_db
    return get_db()["Cartera"].count_documents(flt)
