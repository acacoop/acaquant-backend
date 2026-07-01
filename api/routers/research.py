"""Router /api/research — vista Análisis Fundamental del módulo Renta Variable.

Consumido por acaquant-web /renta-variable (tab Análisis Fundamental). Read-only.
RBAC a nivel router: `renta-variable` (mismo gate que el Scanner → NO invitado).
Lee research.* (fundamentals de Refinitiv/LSEG). Ver docs/RESEARCH_REFINITIV.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.auth import require_module
from api.services import research_fundamentals as svc

# Accesible como el Scanner (módulo renta-variable, también visible al invitado www):
# son fundamentals de empresas públicas (datos de mercado).
router = APIRouter(
    prefix="/api/research",
    tags=["Research"],
    dependencies=[Depends(require_module("renta-variable"))],
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
