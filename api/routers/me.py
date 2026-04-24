"""Router /api/me — identidad del caller.

Devuelve email + role + módulos permitidos para que el frontend sepa
qué vistas mostrar y a dónde redirigir. Sin gate de módulo (cualquier
user autenticado debe poder consultar su propia identidad).
"""
from fastapi import APIRouter, Depends

from api.auth import get_user_email
from core.roles import get_user_modules, get_user_role

router = APIRouter(tags=["Auth"])


@router.get("/api/me")
def me(email: str = Depends(get_user_email)) -> dict:
    """Identidad + role + módulos del caller.

    Cualquier user autenticado por Cloudflare Access (que pasó verify_api_key)
    puede pegarle a este endpoint. El frontend lo usa para:
      - Mostrar / esconder links del nav según los módulos.
      - Redirigir a /403 si pide una ruta que no tiene en modules.
      - Renderizar la sección admin del Manager solo si is_admin.
    """
    role = get_user_role(email)
    modules = list(get_user_modules(email))
    return {
        "email":    email,
        "role":     role,
        "modules":  modules,
        "is_admin": role == "admin",
    }
