"""Manager sub-router — set de cuentas ACA VALORES (módulo `manager_clientes`).

Tab `/manager → CLIENTES → ACA VALORES`: alta/baja de cuentas del set. El filtro de
la vista OPERACIONES lo usa (Todas / Solo ACA VALORES / Sin ACA VALORES).

Endpoints (prefix /api/manager lo agrega el paquete):
  GET    /aca-valores             → cuentas del set
  GET    /aca-valores/candidatos  → buscador de cuentas para agregar (q)
  POST   /aca-valores             → alta por id_cuenta (idempotente)
  DELETE /aca-valores             → baja por id_cuenta
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.auth import get_user_email
from api.services import aca_valores as _svc

router = APIRouter()


@router.get("/aca-valores")
def list_aca_valores() -> dict:
    return _svc.listar()


@router.get("/aca-valores/candidatos")
def aca_valores_candidatos(q: str = Query("", description="Substring sobre id_cuenta o denominación")) -> dict:
    return _svc.candidatos(q=q)


class _AcaValorNew(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id_cuenta: str = Field(..., min_length=1, max_length=128)


@router.post("/aca-valores")
def add_aca_valor(req: _AcaValorNew = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.agregar(id_cuenta=req.id_cuenta, actor=actor or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/aca-valores")
def delete_aca_valor(id_cuenta: str = Query(..., min_length=1)) -> dict:
    try:
        return _svc.quitar(id_cuenta)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
