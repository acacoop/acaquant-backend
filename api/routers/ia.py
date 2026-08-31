"""api/routers/ia.py — el módulo IA: hoy, SOLO el briefing de apertura.

Solo HTTP plumbing (la lógica vive en api/services/). El gate es
estructural: se monta en api/main.py con `_IA` (bearer + require_module("ia")),
y el prefijo /api/ia ya mapea al módulo en ENDPOINT_MODULE_PREFIXES.

⚠️ **El AV AGENT ya NO vive acá.** Sus 44 endpoints se fueron a
`api/routers/agente.py` con el rediseño 2.0 (`docs/AGENT.md`):
estaban colgados de este router por herencia, no por pertenencia.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services import briefing

router = APIRouter(prefix="/api/ia", tags=["ia"])


@router.get("/briefing")
def briefing_apertura():
    """Briefing de apertura (modal de HOME, 10:00 ART). v1 determinista —
    futuros US, oficial (MAE live + A3500) y cierres MEP/CCL con variaciones."""
    return briefing.briefing_hoy()
