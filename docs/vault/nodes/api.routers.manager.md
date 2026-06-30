---
id: api.routers.manager
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/__init__.py
---

# api/routers/manager/__init__

> Manager API — paquete con sub-routers por sub-dominio.

**Archivo:** `api/routers/manager/__init__.py`

## Qué hace
Paquete `/api/manager` — `__init__.py` arma el `APIRouter` raíz del panel Manager y monta todos los sub-routers (status, checks, jobs, options, logs, users, roles, grupos, aunesa, assets, valuaciones, operaciones, comercial, clientes, compliance) cada uno con su gate RBAC propio. Las tabs admin van detrás del módulo `manager` (admin-only); `comercial`/`clientes`/`compliance` usan gates OR finos para que `asistente_comercial` entre solo a sus tabs sin abrirle el resto.

Conecta con: `api.auth::require_module` / `require_any_module` + `api.deps::verify_api_key` para los gates; importa y monta los 15 sub-routers de `api/routers/manager/`. Se monta en `api.main`. Lo consume la manager-view de acaquant-web.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.deps]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.bonos]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.compliance]]  ·  _module_
- [[api.routers.manager.contrapartes]]  ·  _module_
- [[api.routers.manager.control_automatico]]  ·  _module_
- [[api.routers.manager.diagnostico]]  ·  _module_
- [[api.routers.manager.grupos]]  ·  _module_
- [[api.routers.manager.import_tenencia]]  ·  _module_
- [[api.routers.manager.instrumentos]]  ·  _module_
- [[api.routers.manager.jobs]]  ·  _module_
- [[api.routers.manager.logs]]  ·  _module_
- [[api.routers.manager.ons]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.options]]  ·  _module_
- [[api.routers.manager.renta_variable]]  ·  _module_
- [[api.routers.manager.roles]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.manager.users]]  ·  _module_
- [[api.routers.manager.valuaciones]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.cmp.aunesa-aum-panel]]  ·  _component_
- [[web.cmp.manager-view]]  ·  _component_
- [[web.cmp.recursos-panel]]  ·  _component_
- [[web.lib.proxy]]  ·  _lib_
