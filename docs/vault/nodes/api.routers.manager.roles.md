---
id: api.routers.manager.roles
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\manager\roles.py
---

# api/routers/manager/roles

> Manager sub-router — matriz de roles + audit log.

**Archivo:** `api\routers\manager\roles.py`

## Qué hace
Sub-router `/api/manager/roles` — administra la matriz de roles↔módulos (qué ve cada rol) y su audit log. GET devuelve la matriz completa más los módulos canónicos y la lista de roles; PATCH reemplaza los módulos de un rol (filtra módulos desconocidos para no dejar zombies); `/roles/audit` lista los últimos eventos. Admin-only.

Conecta con: `core.roles` (`MODULES`, `get_matrix`, `set_role_modules`, `list_audit`) que persiste en `Manager.RoleMatrix` y registra en `Manager.RoleAudit`; auth `get_user_email` para el actor del audit. Lo consume la tab ROLES Y PERMISOS de la manager-view.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.manager_infra_sql]]  ·  _module_
- [[core.roles]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
