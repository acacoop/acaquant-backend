"""Router /api/research-fred — tab "Datos Internacionales" (FRED) de Research.

Doc vivo: docs/RESEARCH.md. Gate a nivel router: módulo `research` (interno,
JAMÁS invitado — REGLA #8). Read-only: sirve de `research.fred_*` (el sync lo hace
jobs/fred_research.py). Gotcha: prefijo nuevo ⇒ route handler de Next
(`src/app/api/research-fred/[...path]/route.ts`) + entrada en proxy.ts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import require_module
from api.services import research_fred_sql as svc

router = APIRouter(
    prefix="/api/research-fred",
    tags=["Research FRED"],
    dependencies=[Depends(require_module("research"))],
)


@router.get("/bloques")
def bloques() -> dict:
    """El watch agrupado por bloque (sub-tabs): series con etiqueta/unidad/freq/rango."""
    return svc.bloques()


@router.get("/series")
def series(
    ids: list[str] = Query(..., description="series_id de FRED, ej DGS10 (hasta 16)"),
    desde: str | None = Query(None, description="YYYY-MM-DD (default 12 meses)"),
    hasta: str | None = Query(None),
) -> dict:
    """Series de N ids en una sola respuesta (batch por bloque)."""
    return svc.series(ids=ids, desde=desde, hasta=hasta)
