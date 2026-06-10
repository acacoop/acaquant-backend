---
id: core.grupos
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/grupos.py
---

# core/grupos

> core/grupos.py — grupos de acceso por cuenta (scoping multi-tenant).

**Archivo:** `core/grupos.py`

## Qué hace
Implementa los grupos de acceso por cuenta (scoping multi-tenant). Un grupo asocia usuarios (emails) con cuentas comitentes (id_cuenta). La función central `cuentas_visibles(email)` devuelve el set de cuentas que el usuario puede ver, o `None` cuando no hay restricción (admin, o usuario sin grupo asignado — transición fail-open). Cachea por email con TTL de 60s y `invalidate_cache` tras cada mutación.

Conecta con: lee/escribe `Manager.Grupos`; usa `core.roles.get_user_role` para detectar admin; lo gestiona el CRUD de `/manager → GRUPOS` y lo enforcean los endpoints de cuentas (vía `api/services/_grupos_scope.py`).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.grupos_sql]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.roles]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.grupos]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
