---
id: db.CashFlow.Productores
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CashFlow.Productores

> Colección Mongo en DB CashFlow.

## Qué hace
Maestro de productores/agentes comerciales en la base `CashFlow`. Catálogo de referencia que asocia cuentas con su productor para segmentación y edición comercial.

Conecta con: lo usan `api/services/segmentacion.py` y `api/routers/manager/clientes.py` (edición de la segmentación comercial de clientes).

## Lo usan (backlinks) ←
- [[api.services.segmentacion]]  ·  _module_
