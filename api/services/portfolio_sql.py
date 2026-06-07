"""api/services/portfolio_sql.py — vista PORTFOLIO / AuM leyendo de Postgres (Supabase).

Servicio PURO (sin FastAPI). Espejo SQL de `api/services/portfolio.py`. Mismo shape de salida
→ dual-run + comparación (scripts/compare_portfolio_sql_vs_mongo.py). Ver
docs/MIGRACION_MONGO_SUPABASE.md.

Reglas: AuM "último snapshot" = `max(fecha_snapshot)` GLOBAL; exclusión de la cuenta 255 de la
VISTA (no de la persistencia) con `id_cuenta IS DISTINCT FROM '255'`; valuación ya viene
calculada en `aum.valuacion` (ARS); Decimal→float; fecha date→ISO. El AuM excluye sus filtros
de persistencia (jobs/_aum_filters) en la escritura, no acá.

Estado: Chunk 1 (raw: listar_aum, listar_cuentas). Agregados (total_*, fci_*) y renta fija/CER
+ PnL en progreso.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _f(x):
    return float(x) if x is not None else None


def _dt(d):
    """date → datetime a medianoche (mismo contrato que portfolio._fecha_dt: AumAPI servía
    `fecha` como datetime). None si d es None."""
    return datetime(d.year, d.month, d.day) if d is not None else None


def _max_snap() -> object | None:
    return _q("SELECT max(fecha_snapshot) AS f FROM aum")[0]["f"]


def listar_aum(id_cuenta: str | None = None, unidad: str | None = None,
               cuenta: str | None = None, desde: str | None = None, hasta: str | None = None,
               ultimo: bool = False, scope: tuple[str, ...] | None = None) -> list:
    conds: list[str] = []
    p: dict = {}
    if ultimo:
        last = _max_snap()
        if last is None:
            return []
        conds.append("fecha_snapshot = %(f)s")
        p["f"] = last
    if id_cuenta:
        conds.append("id_cuenta = %(idc)s")
        p["idc"] = id_cuenta
    elif scope is not None:
        conds.append("id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    if unidad:
        conds.append("unidad = %(u)s")
        p["u"] = unidad
    if cuenta:
        conds.append("cuenta = %(c)s")
        p["c"] = cuenta
    if not ultimo and (desde or hasta):
        if desde:
            conds.append("fecha_snapshot >= %(desde)s")
            p["desde"] = desde
        if hasta:
            conds.append("fecha_snapshot <= %(hasta)s")
            p["hasta"] = hasta
    where = " AND ".join(conds) if conds else "TRUE"
    rows = _q(f"SELECT fecha_snapshot, id_cuenta, unidad, cantidad, cuenta, precio, valuacion "
              f"FROM aum WHERE {where}", p)
    return [{
        "fecha": _dt(r["fecha_snapshot"]), "id_cuenta": r["id_cuenta"], "unidad": r["unidad"],
        "cantidad": _f(r["cantidad"]), "cuenta": r["cuenta"], "precio": _f(r["precio"]),
        "valuacion": _f(r["valuacion"]),
    } for r in rows]


def listar_cuentas(scope: tuple[str, ...] | None = None) -> list[dict]:
    last = _max_snap()
    if last is None:
        return []
    p: dict = {"f": last}
    sc = ""
    if scope is not None:
        sc = " AND id_cuenta = ANY(%(scope)s)"
        p["scope"] = list(scope)
    return [{"id_cuenta": r["id_cuenta"], "cuenta": r["cuenta"]} for r in _q(
        f"SELECT id_cuenta, max(cuenta) AS cuenta FROM aum "
        f"WHERE fecha_snapshot = %(f)s{sc} GROUP BY id_cuenta ORDER BY id_cuenta", p)]
