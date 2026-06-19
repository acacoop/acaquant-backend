"""Manager API — paquete con sub-routers por sub-dominio.

Este `__init__.py` expone `router` (un `APIRouter` con prefix `/api/manager`)
que agrega todos los sub-routers. El orden de `include_router` es relevante
sólo en `jobs.py` internamente (catch-all `/jobs/{id}` definido después de
`/jobs/history` y `/jobs/history/stats`).

RBAC por sub-router (gate fino para `asistente_comercial`):
  - Tabs admin (status/checks/jobs/options/asistente/logs/users/roles/grupos/
    aunesa/assets/valuaciones) → `manager` (umbrella, admin-only).
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

from api.auth import require_any_module, require_module
from api.deps import verify_api_key
from api.routers.manager import (
    assets,
    aunesa,
    bonos,
    checks,
    clientes,
    compliance,
    contrapartes,
    control_automatico,
    diagnostico,
    grupos,
    import_tenencia,
    instrumentos,
    jobs,
    logs,
    ons,
    operaciones,
    options,
    roles,
    status,
    users,
    valuaciones,
)

router = APIRouter(prefix="/api/manager", tags=["Manager"])

# Gates con OR (manager umbrella ∨ sub-módulo): admin (con `manager` en Mongo)
# entra a todo SIN necesidad de migrar la matriz; `asistente_comercial` (con
# `manager_comercial` y `manager_clientes` pero SIN `manager`) entra a sus 2
# tabs. Las tabs admin (status/jobs/etc) siguen requiriendo `manager`.
_MGR             = [Depends(verify_api_key), Depends(require_module("manager"))]
_CLIENTES        = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_clientes")))]
_CLIENTES_BULK   = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_clientes_bulk")))]
_COMPLIANCE      = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_compliance")))]
_TITULOS         = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_titulos")))]
_INSTRUMENTOS    = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_titulos", "manager_instrumentos")))]
_CONTRAPARTES    = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_contrapartes")))]
_AUNESA          = [Depends(verify_api_key), Depends(require_any_module(("manager", "manager_aunesa")))]

# Tabs admin (umbrella `manager`):
router.include_router(status.router,      dependencies=_MGR)
router.include_router(diagnostico.router, dependencies=_MGR)
router.include_router(checks.router,      dependencies=_MGR)
router.include_router(jobs.router,        dependencies=_MGR)
router.include_router(options.router,     dependencies=_MGR)
router.include_router(logs.router,        dependencies=_MGR)
router.include_router(users.router,       dependencies=_MGR)
router.include_router(roles.router,       dependencies=_MGR)
router.include_router(grupos.router,      dependencies=_MGR)
router.include_router(aunesa.router,      dependencies=_MGR)
router.include_router(valuaciones.router, dependencies=_MGR)
router.include_router(operaciones.router, dependencies=_MGR)
router.include_router(import_tenencia.router, dependencies=_AUNESA)

# Tabs accesibles a `asistente_comercial`:
router.include_router(clientes.router,            dependencies=_CLIENTES)
router.include_router(control_automatico.router,  dependencies=_CLIENTES)
router.include_router(clientes.bulk_router,       dependencies=_CLIENTES_BULK)
router.include_router(compliance.router,      dependencies=_COMPLIANCE)
router.include_router(assets.router,          dependencies=_TITULOS)
router.include_router(ons.router,             dependencies=_TITULOS)
router.include_router(bonos.router,           dependencies=_TITULOS)
router.include_router(instrumentos.router,    dependencies=_INSTRUMENTOS)
router.include_router(contrapartes.router,    dependencies=_CONTRAPARTES)
