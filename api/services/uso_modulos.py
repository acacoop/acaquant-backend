"""api/services/uso_modulos.py — lectura de la telemetría de uso (Manager → USO).

Doc: docs/OBSERVABILIDAD_ROBUSTEZ.md (commit 1). Lee `manager.uso_modulos`
(contador usuario × módulo × hora que escribe api/telemetria.py) y arma la
matriz para el heatmap. Puro (sin FastAPI). Query scopeada por el índice de
`hora` (REGLA #4) — nunca escanea la tabla entera.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def _matriz(rows: list[tuple[str, str, int]]) -> dict:
    """(email, modulo, hits) agregados → {usuarios, modulos, celdas, totales}.
    Pura (testeable). Módulos ordenados por total desc (los más usados primero);
    usuarios por su total desc."""
    celdas: dict[str, dict[str, int]] = {}
    tot_mod: dict[str, int] = {}
    tot_user: dict[str, int] = {}
    for email, modulo, hits in rows:
        celdas.setdefault(email, {})[modulo] = celdas.get(email, {}).get(modulo, 0) + hits
        tot_mod[modulo] = tot_mod.get(modulo, 0) + hits
        tot_user[email] = tot_user.get(email, 0) + hits
    modulos = sorted(tot_mod, key=lambda m: -tot_mod[m])
    usuarios = sorted(tot_user, key=lambda u: -tot_user[u])
    return {
        "usuarios": usuarios,
        "modulos": modulos,
        "celdas": celdas,
        "totales_modulo": tot_mod,
        "totales_usuario": tot_user,
        "total": sum(tot_mod.values()),
    }


def get_uso(dias: int = 7) -> dict:
    """Matriz usuario × módulo de los últimos `dias`. Nunca levanta (vacío si
    la tabla no existe todavía o SQL falla)."""
    dias = max(1, min(int(dias), 90))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT email, modulo, sum(hits)::int FROM manager.uso_modulos "
                "WHERE hora >= now() - make_interval(days => %s) "
                "GROUP BY email, modulo",
                (dias,),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("uso_modulos.get_uso falló (%s)", e)
        rows = []
    return {"dias": dias, **_matriz(rows)}
