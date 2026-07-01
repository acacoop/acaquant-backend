"""Router /api/research — vista Análisis Fundamental del módulo Renta Variable.

Consumido por acaquant-web /renta-variable (tab Análisis Fundamental). Read-only.
RBAC a nivel router: `renta-variable` (mismo gate que el Scanner → NO invitado).
Lee research.* (fundamentals de Refinitiv/LSEG). Ver docs/RESEARCH_REFINITIV.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.auth import is_guest_portal, require_module
from api.services import research_fundamentals as svc


def _deny_guest(request: Request) -> None:
    """Bloquea el portal INVITADO (www). `renta-variable` está en INVITADO_MODULES
    (el invitado ve el Scanner), pero Análisis Fundamental es interno (REGLA #8)."""
    if is_guest_portal(request):
        raise HTTPException(403, "Análisis Fundamental no está disponible para invitados")


router = APIRouter(
    prefix="/api/research",
    tags=["Research"],
    dependencies=[Depends(require_module("renta-variable")), Depends(_deny_guest)],
)


@router.get("/companies")
def companies():
    """Universo de empresas con fundamentals cargados — para el selector.

    Returns:
        list[{ric, ticker, nombre, sector}]
    """
    return svc.list_companies()


@router.get("/fundamentals")
def fundamentals(
    ric: str = Query(..., description="RIC de la empresa (ej. RKLB.O)"),
    freq: str = Query("FY", description="FY (anual) | Q (trimestral)"),
):
    """Datos de los 4 paneles de Análisis Fundamental para un RIC.

    Returns:
        {company, market, periodos, evolucion, margenes, ratios, segmentos}
    """
    return svc.get_analisis(ric=ric, freq=freq)
