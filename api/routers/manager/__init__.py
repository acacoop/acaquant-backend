"""Manager API — paquete con sub-routers por sub-dominio.

Este `__init__.py` expone `router` (un `APIRouter` con prefix `/api/manager`)
que agrega todos los sub-routers. El orden de `include_router` es relevante
sólo en `jobs.py` internamente (catch-all `/jobs/{id}` definido después de
`/jobs/history` y `/jobs/history/stats`).

RBAC por sub-router (gate fino para `asistente_comercial`):
  - Tabs admin (status/checks/jobs/options/asistente/logs/users/roles/grupos/
    aunesa/assets/valuaciones) → `manager` (umbrella, admin-only).
  - `comercial.router` (GETs)                       → `manager_comercial`
  - `clientes.router` (GETs + PATCH fila a fila)    → `manager_clientes`
  - `clientes.bulk_router` (POST /bulk + /bulk-fondeo) → `manager_clientes_bulk`

El gate antes era global en `api/main.py` (`require_module("manager")` para todo
`/api/manager/*`). Se splittea acá para que `asistente_comercial` (que NO tiene
`manager`) pueda entrar a sus 2 tabs sin abrirle el resto del panel. Si agregás
un sub-router nuevo, AGREGÁ su dependency abajo — sin gate explícito queda
abierto a cualquier user autenticado (ver `api/main.py` donde se monta el
paquete sin deps globales).
"""
from fastapi import APIRouter, Depends

from api.auth import require_module
from api.deps import verify_api_key
from api.routers.manager import (
    asistente,
    assets,
    aunesa,
    checks,
    clientes,
    comercial,
    grupos,
    jobs,
    logs,
    options,
    roles,
    status,
    users,
    valuaciones,
)

router = APIRouter(prefix="/api/manager", tags=["Manager"])

_MGR             = [Depends(verify_api_key), Depends(require_module("manager"))]
_COMERCIAL       = [Depends(verify_api_key), Depends(require_module("manager_comercial"))]
_CLIENTES        = [Depends(verify_api_key), Depends(require_module("manager_clientes"))]
_CLIENTES_BULK   = [Depends(verify_api_key), Depends(require_module("manager_clientes_bulk"))]

# Tabs admin (umbrella `manager`):
router.include_router(status.router,      dependencies=_MGR)
router.include_router(checks.router,      dependencies=_MGR)
router.include_router(jobs.router,        dependencies=_MGR)
router.include_router(options.router,     dependencies=_MGR)
router.include_router(asistente.router,   dependencies=_MGR)
router.include_router(logs.router,        dependencies=_MGR)
router.include_router(users.router,       dependencies=_MGR)
router.include_router(roles.router,       dependencies=_MGR)
router.include_router(grupos.router,      dependencies=_MGR)
router.include_router(aunesa.router,      dependencies=_MGR)
router.include_router(assets.router,      dependencies=_MGR)
router.include_router(valuaciones.router, dependencies=_MGR)

# Tabs accesibles a `asistente_comercial`:
router.include_router(comercial.router,       dependencies=_COMERCIAL)
router.include_router(clientes.router,        dependencies=_CLIENTES)
router.include_router(clientes.bulk_router,   dependencies=_CLIENTES_BULK)
