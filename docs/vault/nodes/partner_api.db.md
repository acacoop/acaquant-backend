---
id: partner_api.db
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api/db.py
---

# partner_api/db

> Conexión Mongo del partner_api — singleton read-only a la base `ACAPortfolio`.

**Archivo:** `partner_api/db.py`

## Qué hace
Conexión Mongo del servicio: singleton thread-safe a la base `ACAPortfolio`. Usa `PARTNER_MONGO_URI`, que apunta a un usuario Mongo con permiso de SOLO lectura scopeado únicamente a esa base — así, aunque el proceso se comprometa entero, no puede leer otras bases ni escribir. Pool chico (maxPoolSize=5) con compresión.

Conecta con: lee `PARTNER_MONGO_URI` y `DB_NAME` de `partner_api.settings`; expone `get_db()` que usan `partner_api.auth` (colección `ApiUsers`) y `partner_api.routes` (colección `Cartera`).

## Usa / conecta con →
- [[partner_api.settings]]  ·  _module_

## Lo usan (backlinks) ←
- [[partner_api.auth]]  ·  _module_
- [[partner_api.odata]]  ·  _module_
- [[partner_api.routes]]  ·  _module_
