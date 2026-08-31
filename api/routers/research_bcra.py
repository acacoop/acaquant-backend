"""Router /api/research-bcra — tab BCRA de la vista Research.

Doc vivo: docs/RESEARCH.md. Gate a nivel router: módulo `research` (interno,
JAMÁS invitado — REGLA #8). Read-only: sirve de `research.bcra_*` (el sync lo
hace jobs/bcra_research.py). Recordar el gotcha: prefijo nuevo ⇒ route handler
de Next (`src/app/api/research-bcra/[...path]/route.ts`) + entrada en proxy.ts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import require_module
from api.services import research_bcra_sql as svc

router = APIRouter(
    prefix="/api/research-bcra",
    tags=["Research BCRA"],
    dependencies=[Depends(require_module("research"))],
)


@router.get("/bloques")
def bloques() -> dict:
    """El watch agrupado por bloque (sub-tabs): series con etiqueta/unidad/rango."""
    return svc.bloques()


@router.get("/series")
def series(
    ids: list[int] = Query(..., description="idVariable del BCRA (hasta 12)"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default 12 meses)"),
    hasta: str | None = Query(None),
) -> dict:
    """Series de N variables en una sola respuesta (batch por bloque)."""
    return svc.series(ids=ids, desde=desde, hasta=hasta)
