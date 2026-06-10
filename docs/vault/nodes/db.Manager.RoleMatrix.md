---
id: db.Manager.RoleMatrix
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Manager.RoleMatrix

> Colección Mongo en DB Manager.

## Qué hace
Matriz de permisos por módulo y rol (RBAC), en la base `Manager`. Define qué rol puede ver/usar cada módulo de la plataforma; es la fuente de verdad de la autorización de toda la API.

Conecta con: la lee `core/roles.py` (motor RBAC) que consultan `api/main.py` y los routers protegidos (scanner, derivados_agro, etc.); cambios quedan registrados en `Manager.RoleAudit`.

## Lo usan (backlinks) ←
- [[core.roles]]  ·  _module_
- [[jobs.sync_postgres]]  ·  _module_
