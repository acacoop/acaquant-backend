"""Manager sub-router — CRUD de usuarios.

Endpoints (todos `require_module('manager')` vía gate del padre):
  GET    /api/manager/users             → list
  POST   /api/manager/users             → create (o upsert por email)
  PATCH  /api/manager/users/{email}     → update role / enabled / notes
  DELETE /api/manager/users/{email}     → delete

El modelo de audit (Manager.RoleAudit) queda cubierto: cada mutación
invoca `core.roles.upsert_user / delete_user` que loggea who/what/when.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from api.auth import get_user_email
from core.roles import (
    delete_user as _delete_user,
)
from core.roles import (
    get_matrix,
    list_users,
    upsert_user,
)

router = APIRouter()


class _UserCreate(BaseModel):
    email: EmailStr
    role: str = Field(..., min_length=1, max_length=32)
    enabled: bool = True
    notes: str | None = None


class _UserPatch(BaseModel):
    role: str | None = Field(None, min_length=1, max_length=32)
    enabled: bool | None = None
    control_comercial: bool | None = None
    notes: str | None = None


def _validate_role(role: str) -> None:
    matrix = get_matrix()
    if role not in matrix:
        raise HTTPException(
            status_code=400,
            detail=f"role desconocido: {role!r} (válidos: {list(matrix)})",
        )


@router.get("/users")
def list_users_endpoint() -> dict:
    """Todos los usuarios + los roles disponibles en la matriz vigente."""
    return {
        "users": list_users(),
        "roles": list(get_matrix().keys()),
    }


@router.post("/users", status_code=201)
def create_user_endpoint(
    req: _UserCreate = Body(...),
    actor: str = Depends(get_user_email),
):
    """Crea (o upsertea por email) un user. Audit-log incluido."""
    _validate_role(req.role)
    return upsert_user(
        email=req.email,
        role=req.role,
        enabled=req.enabled,
        notes=req.notes,
        actor=actor,
    )


@router.patch("/users/{email}")
def patch_user_endpoint(
    email: str,
    req: _UserPatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Update de role, enabled o notes. Si no se pasa nada, no-op."""
    if req.role is not None:
        _validate_role(req.role)
    # Resolvemos el estado actual para preservar campos no pasados.
    current = {u["email"]: u for u in list_users()}.get(email.lower().strip())
    if not current:
        raise HTTPException(status_code=404, detail=f"user {email!r} no encontrado")

    return upsert_user(
        email=email,
        role=req.role if req.role is not None else current["role"],
        enabled=req.enabled if req.enabled is not None else current.get("enabled", True),
        control_comercial=(req.control_comercial if req.control_comercial is not None
                           else current.get("control_comercial", False)),
        notes=req.notes if req.notes is not None else current.get("notes", ""),
        actor=actor,
    )


@router.delete("/users/{email}", status_code=204)
def delete_user_endpoint(
    email: str,
    actor: str = Depends(get_user_email),
):
    """Elimina un user. Self-delete permitido (es responsabilidad del admin)."""
    if not _delete_user(email=email, actor=actor):
        raise HTTPException(status_code=404, detail=f"user {email!r} no encontrado")
    return None
