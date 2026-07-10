"""api/routers/ia.py — endpoints del módulo IA (QuantAI, docs/QUANTAI.md).

Solo HTTP plumbing (la lógica vive en api/services/ia_obs.py). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services import briefing, ia_obs

router = APIRouter(prefix="/api/ia", tags=["ia"])


@router.get("/observabilidad")
def observabilidad(dias: int = 14, limit: int = 30):
    """Trazas del gateway de IA para OBSERVABILIDAD → IA: resumen de hoy
    (+% presupuesto), serie por día, agregado por tarea y últimas llamadas."""
    return ia_obs.observabilidad(dias=dias, limit=limit)


@router.get("/briefing")
def briefing_apertura():
    """Briefing de apertura (modal de HOME, 10:00 ART). v1 determinista —
    futuros US, oficial (MAE live + A3500) y cierres MEP/CCL con variaciones."""
    return briefing.briefing_hoy()
