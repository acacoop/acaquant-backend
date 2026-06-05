"""POST /api/manager/control-automatico/* — conciliación Excel↔cuentas + segmentar.

Parte de la tab CLIENTES (gate manager ∨ manager_clientes). Reconcilia un Excel
de CUITs contra Clientes.Comitentes y permite segmentar las matcheadas a
nivel_1 = PRODUCTORES.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import control_automatico as svc

router = APIRouter()


class ReconciliarIn(BaseModel):
    cuits: list[str] = Field(..., description="CUITs del Excel (col 'Nº ident.fis.1').")


class SegmentarIn(BaseModel):
    id_cuentas: list[str] = Field(..., description="id_cuenta de las cuentas a marcar PRODUCTORES.")


@router.post("/control-automatico/reconciliar")
def reconciliar(data: ReconciliarIn) -> dict:
    """Cruza los CUITs del Excel contra nuestras cuentas → tenemos / no_tenemos."""
    return svc.reconciliar(data.cuits)


@router.post("/control-automatico/segmentar")
def segmentar(data: SegmentarIn, email: str = Depends(get_user_email)) -> dict:
    """Setea nivel_1 = PRODUCTORES en las cuentas dadas (las que tenemos)."""
    return svc.segmentar(data.id_cuentas, actor=email)
