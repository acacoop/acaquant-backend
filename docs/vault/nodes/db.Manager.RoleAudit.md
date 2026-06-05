---
id: db.Manager.RoleAudit
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Manager.RoleAudit

> Colección Mongo en DB Manager.

## Qué hace
Log de auditoría de cambios en la matriz de roles, en la base `Manager`. Registra quién modificó qué permiso de qué rol y cuándo, para trazabilidad del control de acceso.

Conecta con: la escriben `core/roles.py` y `api/routers/manager/users.py` ante cada cambio de roles/usuarios; se consulta desde el sub-router de roles del Manager.

## Lo usan (backlinks) ←
- [[core.roles]]  ·  _module_
- [[scripts.db_maintenance]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
- [[scripts.seed_asistente_comercial]]  ·  _module_
