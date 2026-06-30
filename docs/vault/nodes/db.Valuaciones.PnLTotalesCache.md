---
id: db.Valuaciones.PnLTotalesCache
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Valuaciones.PnLTotalesCache

> Colección Mongo en DB Valuaciones.

## Qué hace
Cache del PnL total por cuenta en la base `Valuaciones`. Precalcula el PnL (cost-basis weighted-average) de TODAS las cuentas para servir la vista sin recomputar el motor en cada request.

Conecta con: la escribe el cron `jobs/pnl_totales_precompute.py`; la lee `api/services/pnl.py`. Referenciada como singleton-friendly en `core/mongo.py`.

## Lo usan (backlinks) ←
- [[api.services.diagnostico_registry]]  ·  _module_
