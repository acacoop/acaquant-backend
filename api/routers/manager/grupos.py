"""Manager sub-router — grupos de acceso por cuenta.

Endpoints (todos admin-only — el paquete `manager` ya va gateado por
`require_module("manager")` en `api/main.py`):
  GET    /api/manager/grupos           → grupos + cuentas reales para el selector
  POST   /api/manager/grupos           → crear grupo
  PATCH  /api/manager/grupos/{id}      → editar grupo
  DELETE /api/manager/grupos/{id}      → eliminar grupo

FASE 1 — solo CRUD. El enforcement (aplicar `cuentas_visibles` en los
endpoints de cuentas) es Fase 2.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_user_email
from core.grupos import actualizar_grupo, crear_grupo, eliminar_grupo, listar_grupos

router = APIRouter()


class _GrupoBody(BaseModel):
    nombre:     str
    emails:     list[str] = []
    id_cuentas: list[str] = []


@router.get("/grupos")
def get_grupos() -> dict:
    """Grupos existentes + las cuentas reales (último snapshot AuM) para que
    el panel arme el selector. Las cuentas son las que YA existen — no se
    puede asignar a un grupo una cuenta inventada."""
    from api.services.portfolio import listar_cuentas

    return {
        "grupos":  listar_grupos(),
        "cuentas": listar_cuentas(),   # [{id_cuenta, cuenta}, ...]
    }


@router.post("/grupos")
def post_grupo(
    req: _GrupoBody = Body(...),
    actor: str = Depends(get_user_email),
) -> dict:
    """Crea un grupo. `emails` se lowercasean, `id_cuentas` se deduplican."""
    try:
        return crear_grupo(req.nombre, req.emails, req.id_cuentas, actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/grupos/{grupo_id}")
def patch_grupo(
    grupo_id: str,
    req: _GrupoBody = Body(...),
    actor: str = Depends(get_user_email),
) -> dict:
    """Reemplaza nombre/emails/cuentas del grupo."""
    try:
        return actualizar_grupo(grupo_id, req.nombre, req.emails, req.id_cuentas, actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/grupos/{grupo_id}")
def delete_grupo(grupo_id: str) -> dict:
    """Elimina el grupo. Los usuarios que estaban solo en ese grupo vuelven
    a ver TODO (sin grupo = sin restricción)."""
    try:
        ok = eliminar_grupo(grupo_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="grupo no encontrado")
    return {"ok": True}
