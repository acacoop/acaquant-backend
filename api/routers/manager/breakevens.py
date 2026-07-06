"""Manager sub-router — Títulos → Breakevens (curaduría de pares).

Permite EXCLUIR pares Lecap↔CER mal emparejados por el motor (auto por vto más
cercano). Los excluidos se filtran en la lectura pública (no se toca el motor).
Gate `manager_titulos`.

  GET  /api/manager/breakevens/pares      → matriz de pares vivos + flag excluido
  POST /api/manager/breakevens/exclusion  → excluir / reincluir un par
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
