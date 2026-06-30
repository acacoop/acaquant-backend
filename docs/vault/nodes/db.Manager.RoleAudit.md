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

_Sin conexiones detectadas mecánicamente._
