"""Sub-router Manager → /api/manager/comercial — Tablero Comercial.

v1 (solo manager): lente POR OPERADOR. Hereda el gate `_MANAGER` del paquete
(`require_module("manager")`). Cuando se abra a operadores con scope, se
extrae a su propio módulo RBAC `comercial`. Ver docs/TABLERO_COMERCIAL.md [5].
"""
from typing import Any

from fastapi import APIRouter, Query

from api.services.comercial import resumen_por_operador

router = APIRouter()


@router.get("/comercial/operadores")
def comercial_operadores(
    dias_activa: int = Query(30, ge=1, le=365, description="≤ este nº de días sin operar = ACTIVA"),
    dias_dormida: int = Query(90, ge=1, le=730, description="> este nº de días = DORMIDA"),
) -> dict[str, Any]:
    """Resumen comercial por operador: # cuentas, activas/dormidas, AuM, etc."""
    return resumen_por_operador(dias_activa=dias_activa, dias_dormida=dias_dormida)
