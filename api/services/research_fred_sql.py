"""api/services/research_fred_sql.py — lectura de las series FRED para la tab
"Datos Internacionales" de la vista Research (docs/RESEARCH.md). Puro (sin
FastAPI); todo sale de la DB (0 requests a FRED por carga — el sync lo hace
jobs/fred_research.py).

Eficiencia: `series()` es UNA query batch para N ids (la vista pide un bloque por
request, no una serie por request). series_id es TEXTO (ej DGS10), no int.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from api.cache import cached
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_MAX_IDS = 16


@cached(ttl=300)
def bloques() -> dict:
    """El watch agrupado por bloque (las sub-tabs de la vista): qué series tiene
    cada bloque, con etiqueta/unidad/freq y el rango de historia disponible."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.bloque, w.series_id, w.etiqueta, w.unidad, w.freq, w.orden,
                       min(o.fecha) AS desde, max(o.fecha) AS hasta
                FROM research.fred_watch w
                LEFT JOIN research.fred_observations o ON o.series_id = w.series_id
                WHERE w.activo
                GROUP BY w.bloque, w.series_id, w.etiqueta, w.unidad, w.freq, w.orden
                ORDER BY w.bloque, w.orden NULLS LAST, w.series_id
                """
            )
            filas = cur.fetchall()
    except Exception as e:
        logger.warning("research_fred_sql.bloques falló (%s)", e)
        return {"bloques": []}
    grupos: dict[str, list] = {}
    for bloque, sid, etiqueta, unidad, freq, _orden, desde, hasta in filas:
        grupos.setdefault(bloque, []).append({
            "id": sid, "etiqueta": etiqueta, "unidad": unidad, "freq": freq,
            "desde": desde.isoformat() if desde else None,
            "hasta": hasta.isoformat() if hasta else None,
        })
    return {"bloques": [{"bloque": b, "series": s} for b, s in grupos.items()]}


def series(ids: list[str], desde: str | None = None, hasta: str | None = None) -> dict:
    """Series de N ids (texto) en UNA query. {series: [{id, puntos: [[fecha, valor]]}]}.
    Default: últimos 12 meses."""
    ids = [str(i) for i in (ids or [])][:_MAX_IDS]
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
                "SELECT series_id, fecha, valor FROM research.fred_observations "
                "WHERE series_id = ANY(%s) AND fecha BETWEEN %s AND %s "
                "AND valor IS NOT NULL ORDER BY series_id, fecha",
                (ids, d0, d1),
            )
            por_id: dict[str, list] = {}
            for sid, fecha, valor in cur.fetchall():
                por_id.setdefault(sid, []).append([fecha.isoformat(), valor])
    except Exception as e:
        logger.warning("research_fred_sql.series falló (%s)", e)
        return {"series": []}
    return {"desde": d0.isoformat(), "hasta": d1.isoformat(),
            "series": [{"id": i, "puntos": por_id.get(i, [])} for i in ids]}
