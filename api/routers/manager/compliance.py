"""Manager sub-router — COMPLIANCE: operador nuestro vs Aunesa.

Tab `/manager → COMPLIANCE` (rol `compliance` + admin). Solo lectura: cruza en
vivo el operador de Aunesa contra el de `Clientes.Comitentes` y marca las
diferencias. No persiste. Gateado por `manager_compliance` (ver
api/routers/manager/__init__.py).
"""
from __future__ import annotations

from fastapi import APIRouter

from api.services import compliance as svc

router = APIRouter()


@router.get("/compliance/operadores")
def compliance_operadores() -> dict:
    """Todas las cuentas con operador nuestro vs Aunesa (live), marcando las que
    difieren. Cache 5 min en el service."""
    return svc.comparar_operadores()
