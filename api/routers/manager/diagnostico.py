"""GET /api/manager/diagnostico — árbol de salud por vista (motores/jobs/apis).

Reemplaza las 3 tablas planas de /status: arma un árbol HOME/OPERAR/MERCADOS/
NEGOCIO/BACK OFFICE/PORTFOLIOS desde el registro único (`api/services/
diagnostico_registry.py`). Gate admin (`manager`) como el resto del Diagnóstico.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services.diagnostico import arbol

router = APIRouter()


@router.get("/diagnostico")
def get_diagnostico() -> dict:
    return arbol()
