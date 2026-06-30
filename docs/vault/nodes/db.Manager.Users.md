---
id: db.Manager.Users
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Manager.Users

> Colección Mongo en DB Manager.

## Qué hace
Maestro de usuarios de la plataforma en la base `Manager`: identidad (email), rol asignado y el mapeo operador↔usuario que permite resolver cuentas huérfanas en el Tablero Comercial.

Conecta con: la lee `api/auth.py` (resuelve rol del caller tras validar el JWT de Cloudflare Access), `core/roles.py` y `api/services/comercial.py`; se administra (CRUD) vía `api/routers/manager/users.py`.

_Sin conexiones detectadas mecánicamente._
