"""api/services/research_bcra_sql.py — lectura de las series BCRA para la tab
BCRA de la vista Research (docs/RESEARCH.md). Puro (sin FastAPI); todo sale
de la DB (0 requests al BCRA por carga — el sync es de jobs/bcra_research.py).

Eficiencia: `series()` es UNA query batch para N ids (la vista pide un bloque por
request, no una serie por request).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from api.cache import cached
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_MAX_IDS = 12


@cached(ttl=300)
def bloques() -> dict:
    """El watch agrupado por bloque (las sub-tabs de la vista): qué series tiene
    cada bloque, con etiqueta/unidad y el rango de historia disponible."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.bloque, w.id_variable, w.etiqueta, w.unidad, w.orden,
                       min(s.fecha) AS desde, max(s.fecha) AS hasta
                FROM research.bcra_watch w
                LEFT JOIN research.bcra_series s ON s.id_variable = w.id_variable
                WHERE w.activo
                GROUP BY w.bloque, w.id_variable, w.etiqueta, w.unidad, w.orden
                ORDER BY w.bloque, w.orden NULLS LAST, w.id_variable
                """
            )
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("research_bcra_sql.bloques falló (%s)", e)
        return {"bloques": []}
    grupos: dict[str, list] = {}
    for bloque, idv, etiqueta, unidad, _orden, desde, hasta in filas:
        grupos.setdefault(bloque, []).append({
            "id": idv, "etiqueta": etiqueta, "unidad": unidad,
            "desde": desde.isoformat() if desde else None,
            "hasta": hasta.isoformat() if hasta else None,
        })
    return {"bloques": [{"bloque": b, "series": s} for b, s in grupos.items()]}


def series(ids: list[int], desde: str | None = None, hasta: str | None = None) -> dict:
    """Series de N variables en UNA query. {series: [{id, puntos: [[fecha, valor]]}]}
    Default: últimos 12 meses."""
    ids = [int(i) for i in (ids or [])][:_MAX_IDS]
    if not ids:
        return {"series": []}
    hoy = datetime.now(UTC).date()

    def _p(s, dflt):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date() if s else dflt
        except (TypeError, ValueError):
            return dflt

    d0, d1 = _p(desde, hoy - timedelta(days=365)), _p(hasta, hoy)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id_variable, fecha, valor FROM research.bcra_series "
                "WHERE id_variable = ANY(%s) AND fecha BETWEEN %s AND %s "
                "AND valor IS NOT NULL ORDER BY id_variable, fecha",
                (ids, d0, d1),
            )
            por_id: dict[int, list] = {}
            for idv, fecha, valor in cur.fetchall():
                por_id.setdefault(idv, []).append([fecha.isoformat(), valor])
    except Exception as e:
        logger.warning("research_bcra_sql.series falló (%s)", e)
        return {"series": []}
    return {"desde": d0.isoformat(), "hasta": d1.isoformat(),
            "series": [{"id": i, "puntos": por_id.get(i, [])} for i in ids]}
