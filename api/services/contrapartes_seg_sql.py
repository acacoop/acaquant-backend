"""api/services/contrapartes_seg_sql.py — espejo SQL (solo LECTURAS) de
contrapartes_seg.py. Para el dual-run de la vista MANAGER → CONTRAPARTES.

La tabla `contrapartes` (id_cuenta, contraparte, segmento) NO tiene `denominacion`
→ se trae con LEFT JOIN a `cuentas` (igual que comercial_sql._ficha_por_cuenta). Las
ESCRITURAS y el conciliador (Aunesa live) NO viven acá — son Mongo-only (ver
contrapartes_seg.py). Mismo shape de salida → comparables con el motor Mongo.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def listar_contrapartes(*, segmento: str | None = None, contraparte: str | None = None,
                        q: str | None = None) -> dict:
    where = ["cp.id_cuenta IS NOT NULL"]
    p: dict = {}
    if segmento:
        where.append("cp.segmento = %(seg)s")
        p["seg"] = segmento
    if contraparte:
        where.append("cp.contraparte = %(cp)s")
        p["cp"] = contraparte
    # Cada palabra de `q` debe estar en denominacion o cuenta (AND, sin orden).
    for i, tok in enumerate((q or "").split()):
        where.append(f"(u.denominacion ILIKE %(t{i})s OR cp.id_cuenta ILIKE %(t{i})s)")
        p[f"t{i}"] = f"%{tok}%"
    rows = _q(
        f"SELECT cp.id_cuenta AS cuenta, u.denominacion, cp.contraparte, cp.segmento "
        f"FROM contrapartes cp LEFT JOIN cuentas u ON u.id_cuenta = cp.id_cuenta "
        f"WHERE {' AND '.join(where)} ORDER BY u.denominacion NULLS LAST LIMIT 5000", p,
    )
    return {"contrapartes": [
        {"cuenta": r["cuenta"], "denominacion": r["denominacion"],
         "contraparte": r["contraparte"], "segmento": r["segmento"]} for r in rows], "n": len(rows)}


def segmentos_distinct() -> dict:
    segs = [r["segmento"] for r in _q(
        "SELECT DISTINCT segmento FROM contrapartes "
        "WHERE segmento IS NOT NULL AND segmento <> '' ORDER BY segmento")]
    cps = [r["contraparte"] for r in _q(
        "SELECT DISTINCT contraparte FROM contrapartes "
        "WHERE contraparte IS NOT NULL AND contraparte <> '' ORDER BY contraparte")]
    return {"segmentos": segs, "contrapartes": cps}
