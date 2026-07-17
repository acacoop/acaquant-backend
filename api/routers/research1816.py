"""Router /api/research1816 — vista RESEARCH (nueva vista principal).

Doc madre: docs/VISTA_RESEARCH.md. Prefijo distinto del `/api/research` existente
(ese es la Análisis Fundamental de RV, gate renta-variable) para no pisarlo — la
vista Research es OTRA cosa (research macro de 1816: mails ahora, market data 1816
después). Gate a nivel router: módulo `research` (interno, JAMÁS invitado — REGLA #8).

Nivel 1 (pilar B — research escrito): sirve el timeline de mails y el buscador
full-text sobre `ia.research`. El pilar A (market data 1816) se suma cuando esté la
API key. Read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import require_module
from api.services import research_sql as svc

router = APIRouter(
    prefix="/api/research1816",
    tags=["Research"],
    dependencies=[Depends(require_module("research"))],
)


@router.get("/mails")
def mails(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """Timeline del research diario (mails de 1816), más nuevo primero.

    Returns:
        {items: [{id, fecha, fuente, asunto, tipo, cuerpo, destilado}], total}
    """
    return svc.listar_research(limit=limit, offset=offset)


@router.get("/mails/buscar")
def buscar(
    q: str = Query(..., min_length=2, description="texto a buscar (full-text español)"),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """Búsqueda full-text sobre el cuerpo del research ('¿qué dijeron del BCRA?').

    Returns:
        {items: [{..., fragmento}], total, q}
    """
    return svc.buscar_research(q=q, limit=limit)
