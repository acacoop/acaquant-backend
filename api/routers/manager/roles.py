"""Manager sub-router — matriz de roles + audit log.

Endpoints:
  GET   /api/manager/roles               → matriz completa + módulos canónicos + roles
  PATCH /api/manager/roles/{role}        → actualiza los módulos de un role
  GET   /api/manager/roles/audit         → últimos N eventos del audit log
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel

from api.auth import get_user_email
from core.roles import MODULES, get_matrix, list_audit, set_role_modules

router = APIRouter()


class _MatrixPatch(BaseModel):
    modules: list[str]


@router.get("/roles")
def get_roles_endpoint() -> dict:
    """Matriz completa + metadata para renderizar el panel."""
    matrix = get_matrix()
    return {
        "modules": list(MODULES),
        "roles":   list(matrix.keys()),
        "matrix":  {role: list(mods) for role, mods in matrix.items()},
    }


@router.patch("/roles/{role}")
def patch_role_modules_endpoint(
    role: str,
    req: _MatrixPatch = Body(...),
    actor: str = Depends(get_user_email),
):
    """Reemplaza la lista de módulos de un role. Módulos desconocidos se
    filtran silenciosamente (fail-safe: evita módulos zombies en la DB).
    Audit-log incluido."""
    return set_role_modules(
        role=role,
        modules=req.modules,
        actor=actor,
    )


@router.get("/roles/audit")
def audit_endpoint(
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    """Últimos N eventos del audit log, del más reciente al más viejo."""
    return list_audit(limit=limit)
