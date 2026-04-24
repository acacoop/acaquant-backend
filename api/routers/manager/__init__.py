"""Manager API — paquete con sub-routers por sub-dominio.

Este `__init__.py` expone `router` (un `APIRouter` con prefix `/api/manager`)
que agrega todos los sub-routers. El orden de `include_router` es relevante
sólo en `jobs.py` internamente (catch-all `/jobs/{id}` definido después de
`/jobs/history` y `/jobs/history/stats`).

Sub-dominios:
- `status`     — GET /status (motores + jobs batch)
- `checks`     — 6× GET /checks/* (consistencia de data)
- `jobs`       — POST /jobs/run, GET /jobs/history, /jobs/history/stats, /jobs/{id}
- `options`    — GET/PUT /options/expiries
- `asistente`  — 4× GET /asistente/* (observabilidad IA)
- `intel`      — 7× /intel/* (ingesta de reportes macro)
- `logs`       — GET /logs?servicio=&lines= (journalctl de services)
- `users`      — CRUD Manager.Users (admin panel)
- `roles`      — matriz Manager.RoleMatrix + audit log
"""
from fastapi import APIRouter

from api.routers.manager import (
    asistente,
    checks,
    intel,
    jobs,
    logs,
    options,
    roles,
    status,
    users,
)

router = APIRouter(prefix="/api/manager", tags=["Manager"])

router.include_router(status.router)
router.include_router(checks.router)
router.include_router(jobs.router)
router.include_router(options.router)
router.include_router(asistente.router)
router.include_router(intel.router)
router.include_router(logs.router)
router.include_router(users.router)
router.include_router(roles.router)
