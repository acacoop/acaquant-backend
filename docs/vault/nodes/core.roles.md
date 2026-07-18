---
id: core.roles
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\roles.py
---

# core/roles

> Roles y matriz de permisos por módulo.

**Archivo:** `core\roles.py`

## Qué hace
El motor de RBAC: Cloudflare Access decide quién entra al sitio; este módulo decide qué módulos ve cada usuario una vez adentro. Resuelve email→role contra `Manager.Users` (con auto-registro en primera visita y fallback a `MANAGER_EMAILS`→admin / `DEFAULT_ROLE`→sales) y role→módulos contra `Manager.RoleMatrix` (con `DEFAULT_MATRIX` de bootstrap). Cachea por 60s, expone `has_access()`/`get_user_modules()`, y registra toda mutación de usuarios o matriz en `Manager.RoleAudit`. Las identidades de máquina/anon caen fail-closed (cero módulos).

Conecta con: lee/escribe `Manager.Users`, `Manager.RoleMatrix`, `Manager.RoleAudit` vía `core.mongo`. Lo consume `api/auth.py` (gate `require_module`) y los sub-routers de `api/routers/manager/` (CRUD de usuarios y roles).

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[core.roles_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.auth]]  ·  _module_
- [[api.routers.manager.roles]]  ·  _module_
- [[api.routers.manager.users]]  ·  _module_
- [[api.routers.me]]  ·  _module_
- [[api.services.copiloto.derivacion]]  ·  _module_
- [[api.services.copiloto.motor]]  ·  _module_
- [[core.grupos]]  ·  _module_
- [[core.roles_sql]]  ·  _module_
