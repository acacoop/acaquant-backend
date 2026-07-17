"""api/services/research_sql.py — lectura del research diario (mails de 1816) para
la vista RESEARCH (pilar B, Nivel 1). Doc madre: docs/VISTA_RESEARCH.md.

Lee `ia.research` (lo escribe jobs/research_mail.py — QuantAI P6): el mail CRUDO
(fuente de verdad) + el DESTILADO del LLM ({resumen, temas, hechos}). Puro (sin
FastAPI). El `tipo` (diario/mensual) se deriva del asunto al vuelo — todavía no es
columna (ver docs/VISTA_RESEARCH.md §5.2).
"""
from __future__ import annotations

import json
import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100


def _tipo_de_asunto(asunto: str | None) -> str:
    """diario / mensual / otro — heurística sobre el asunto (sin columna todavía).
    'El día en pocas líneas' (aunque venga reenviado con 'RV:'/'Fwd:') = diario."""
    a = (asunto or "").lower()
    if "pocas l" in a:              # "pocas líneas" / "pocas lineas"
        return "diario"
    if "mensual" in a or "informe" in a:
        return "mensual"
    return "otro"


def _parse_destilado(v) -> dict | None:
    """El jsonb puede volver ya como dict (psycopg) o como texto — normaliza."""
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _fila(r: dict) -> dict:
    return {
        "id": r["id"],
        "fecha": r["fecha"].isoformat() if r.get("fecha") else None,
        "fuente": r.get("fuente"),
        "asunto": r.get("asunto"),
        "tipo": _tipo_de_asunto(r.get("asunto")),
        "cuerpo": r.get("cuerpo"),
        "destilado": _parse_destilado(r.get("destilado")),
    }


def listar_research(limit: int = 30, offset: int = 0) -> dict:
    """Timeline del research por fecha (más nuevo primero). Devuelve el crudo +
    destilado de cada mail para que la vista muestre resumen/temas/hechos arriba y
    el texto completo expandible."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    offset = max(0, int(offset))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, fecha, fuente, asunto, cuerpo, destilado "
                "FROM ia.research ORDER BY fecha DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (limit, offset),
            )
            cols = [c.name for c in cur.description]
            items = [_fila(dict(zip(cols, row, strict=True))) for row in cur.fetchall()]
            cur.execute("SELECT count(*) FROM ia.research")
            total = cur.fetchone()[0]
    except Exception as e:
        logger.warning("research_sql.listar_research falló (%s)", e)
        return {"items": [], "total": 0}
    return {"items": items, "total": total}


def buscar_research(q: str, limit: int = 30) -> dict:
    """Búsqueda full-text español sobre el cuerpo ('¿qué dijeron del BCRA?').
    Usa el índice GIN ix_ia_research_fts. Devuelve un fragmento resaltado
    (ts_headline) + el mail completo, ordenado por relevancia."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"items": [], "total": 0, "q": q}
    limit = max(1, min(int(limit), _MAX_LIMIT))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, fecha, fuente, asunto, cuerpo, destilado,
                       ts_headline('spanish', cuerpo, plainto_tsquery('spanish', %s),
                                   'StartSel=«, StopSel=», MaxWords=40, MinWords=20, '
                                   'ShortWord=3') AS fragmento
                FROM ia.research
                WHERE to_tsvector('spanish', cuerpo) @@ plainto_tsquery('spanish', %s)
                ORDER BY ts_rank(to_tsvector('spanish', cuerpo),
                                 plainto_tsquery('spanish', %s)) DESC,
                         fecha DESC
                LIMIT %s
                """,
                (q, q, q, limit),
            )
            cols = [c.name for c in cur.description]
            items = []
            for row in cur.fetchall():
                d = dict(zip(cols, row, strict=True))
                fila = _fila(d)
                fila["fragmento"] = d.get("fragmento")
                items.append(fila)
    except Exception as e:
        logger.warning("research_sql.buscar_research falló (%s)", e)
        return {"items": [], "total": 0, "q": q}
    return {"items": items, "total": len(items), "q": q}
