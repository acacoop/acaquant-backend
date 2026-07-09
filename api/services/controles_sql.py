"""api/services/controles_sql.py — lectura del auto-control de calidad de datos.

Servicio PURO (sin FastAPI). Lee `manager.controles_datos` (escrita por
jobs/controles_datos.py) y devuelve las anomalías agrupadas por control, con lo
que Telegram NO muestra: el detalle de los controles privados (cuentas de
clientes). Consumido por GET /api/manager/controles (gate admin).
"""
from __future__ import annotations

from psycopg.rows import dict_row

from core.postgres import get_pool


def listar_controles(incluir_resueltos_dias: int = 7) -> dict:
    """Anomalías vigentes por control + resueltas de los últimos N días."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT control_id, item_key, detalle, first_seen::text, last_seen::text, "
            "resuelto_at::text FROM manager.controles_datos "
            "WHERE resuelto_at IS NULL "
            "   OR resuelto_at >= now() - make_interval(days => %s) "
            "ORDER BY control_id, resuelto_at NULLS FIRST, first_seen",
            (int(incluir_resueltos_dias),))
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        grupo = out.setdefault(r["control_id"], {"activos": [], "resueltos": []})
        destino = "resueltos" if r["resuelto_at"] else "activos"
        grupo[destino].append({
            "item": r["item_key"], "detalle": r["detalle"],
            "desde": r["first_seen"], "visto": r["last_seen"],
            "resuelto": r["resuelto_at"],
        })
    return {"controles": out,
            "totales": {cid: len(g["activos"]) for cid, g in out.items()}}
