"""Router /api/me — identidad del caller.

Devuelve email + role + módulos permitidos para que el frontend sepa
qué vistas mostrar y a dónde redirigir. Sin gate de módulo (cualquier
user autenticado debe poder consultar su propia identidad).
"""
from fastapi import APIRouter, Depends, Request

from api.auth import get_user_email, is_guest_portal
from api.services import aca, mesa_dinero
from core.roles import (
    INVITADO_MODULES,
    get_user_modules,
    get_user_role,
    user_has_control_comercial,
)

router = APIRouter(tags=["Auth"])


@router.get("/api/me")
def me(request: Request, email: str = Depends(get_user_email)) -> dict:
    """Identidad + role + módulos del caller.

    Cualquier user autenticado por Cloudflare Access (que pasó verify_api_key)
    puede pegarle a este endpoint. El frontend lo usa para:
      - Mostrar / esconder links del nav según los módulos.
      - Redirigir a /403 si pide una ruta que no tiene en modules.
      - Renderizar la sección admin del Manager solo si is_admin.

    Portal invitado (www): el rol se fuerza a `invitado` con SOLO los módulos de
    mercado, sin importar el email — así el nav del portal muestra únicamente lo
    público. El gate real de cada endpoint vive en require_module (default-deny).
    """
    if is_guest_portal(request):
        return {
            "email":    email,
            "role":     "invitado",
            "modules":  list(INVITADO_MODULES),
            "is_admin": False,
            "control_comercial": False,
        }
    role = get_user_role(email)
    modules = list(get_user_modules(email))
    # CAPACIDADES per-usuario que el front necesita para filtrar el nav igual que
    # un módulo. NO son módulos del RBAC (no están en core.roles.MODULES ni en la
    # matriz): el permiso lo da una allowlist por email, no el rol. Se publican
    # acá dentro de `modules` para que el nav y src/proxy.ts sigan usando UN solo
    # mecanismo — si fueran un campo aparte habría que duplicar el filtrado en los
    # dos lados. Agregarlas a MODULES sería un error: pondría un checkbox en
    # ROLES Y PERMISOS que no controlaría nada.
    if mesa_dinero.puede_ver(email=email):
        modules.append("mesa-dinero")
    # `aca` SÍ es un módulo del RBAC (lo tiene el rol `empleado_aca`), pero el
    # acceso además se gana por estar en la allowlist de escritura de la mesa —
    # escribir implica ver. Sin esta línea, un escritor sin el rol pasaba el gate
    # del backend y NO veía el link en el nav: entraba solo tipeando la URL.
    if "aca" not in modules and aca.puede_ver(email=email):
        modules.append("aca")
    return {
        "email":    email,
        "role":     role,
        "modules":  modules,
        "is_admin": role == "admin",
        "control_comercial": user_has_control_comercial(email),
    }
