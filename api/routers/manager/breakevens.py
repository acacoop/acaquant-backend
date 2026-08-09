"""Manager sub-router — Títulos → Breakevens (curaduría de pares).

Curaduría en los dos sentidos, sin tocar el motor: EXCLUIR un par mal emparejado
y AGREGAR uno manual (Lecap↔CER elegido a mano) que el motor no arma. Los dos
tienen efecto en la lectura pública al instante. Gate `manager_titulos`.

  GET  /api/manager/breakevens/pares       → matriz de pares vivos + manuales
  POST /api/manager/breakevens/exclusion   → excluir / reincluir un par
  GET  /api/manager/breakevens/candidatos  → bonos tasa_fija y cer para el '+'
  POST /api/manager/breakevens/manual      → agregar / borrar un par manual
  GET  /api/manager/breakevens/diagnostico → por qué cada bono entra o no entra
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import breakevens_admin as svc

router = APIRouter()


@router.get("/breakevens/pares")
def list_pares() -> dict:
    """Todos los pares que arma el motor hoy, con `excluido` por par (para el toggle)."""
    return svc.list_pares_con_estado()


class _ExclusionIn(BaseModel):
    lecap: str = Field(..., min_length=1, max_length=32)   # ticker corto (S13N6)
    cer: str = Field(..., min_length=1, max_length=32)     # ticker corto (TX26)
    excluir: bool = Field(..., description="True = ocultar el par; False = reincluir")


@router.post("/breakevens/exclusion")
def set_exclusion(req: _ExclusionIn = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return svc.set_exclusion(
            lecap=req.lecap, cer=req.cer, excluir=req.excluir, email=actor or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/breakevens/candidatos")
def list_candidatos() -> dict:
    """Los dos combos del '+': `tasa_fija` de un lado, `cer` del otro."""
    return svc.candidatos()


class _ManualIn(BaseModel):
    lecap: str = Field(..., min_length=1, max_length=32)   # ticker corto tasa_fija
    cer: str = Field(..., min_length=1, max_length=32)     # ticker corto CER
    agregar: bool = Field(True, description="True = crear el par; False = borrarlo")


@router.post("/breakevens/manual")
def set_manual(req: _ManualIn = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Crea (o borra) un par Lecap↔CER que el motor no arma. El BE se calcula en
    la lectura, así que aparece en Renta Fija sin reiniciar nada."""
    try:
        return svc.set_manual(
            lecap=req.lecap, cer=req.cer, agregar=req.agregar, email=actor or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/breakevens/diagnostico")
def diagnostico() -> dict:
    """Cobertura: por qué cada bono `tasa_fija` del master entra o no a la matriz."""
    return svc.diagnostico()
