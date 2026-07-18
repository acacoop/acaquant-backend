"""Manager sub-router — telemetría de USO (usuario × módulo).

Tab Manager → OBSERVABILIDAD → USO. Solo plumbing; la lógica vive en
api/services/uso_modulos.py. Gate `manager` (umbrella) en el __init__ del
paquete. Doc: docs/OBSERVABILIDAD_ROBUSTEZ.md (commit 1).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import uso_modulos as svc

router = APIRouter()


@router.get("/uso")
def uso(dias: int = Query(7, ge=1, le=90)) -> dict:
    """Matriz usuario × módulo con hits del rango + totales por módulo/usuario.

    Returns:
        {dias, usuarios, modulos, celdas: {email: {modulo: hits}},
         totales_modulo, totales_usuario, total}
    """
    return svc.get_uso(dias=dias)
