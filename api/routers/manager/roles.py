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
from core.roles import DEFAULT_MATRIX, MODULES, get_matrix, list_audit, set_role_modules

router = APIRouter()


class _MatrixPatch(BaseModel):
    modules: list[str]


@router.get("/roles")
def get_roles_endpoint() -> dict:
    """Matriz completa + metadata para renderizar el panel.

    `roles` une los de la DB con los declarados en DEFAULT_MATRIX. Un rol nuevo
    en el código (ej. `empleado_aca`) NO existe en `manager.role_matrix` de prod
    hasta que alguien le asigna un módulo — y sin aparecer en esta lista el
    panel no lo mostraba, así que no había forma de asignárselo a nadie: un rol
    invisible que solo se podía crear a mano en SQL.

    Los módulos de esos roles van VACÍOS a propósito. `get_matrix()` es la
    verdad efectiva (lo que la DB concede); mostrar los defaults del código como
    si estuvieran otorgados haría que el panel prometa accesos que el gate no da.
    """
    matrix = get_matrix()
    roles = sorted(set(matrix) | set(DEFAULT_MATRIX))
    return {
        "modules": list(MODULES),
        "roles":   roles,
        "matrix":  {role: list(matrix.get(role, ())) for role in roles},
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
    """Últimos N eventos del audit log, del más reciente al más viejo
    (manager.role_audit)."""
    return list_audit(limit=limit)
