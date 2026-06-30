---
id: db.Valuaciones.ConsolidadoCuentas
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Valuaciones.ConsolidadoCuentas

> Colección Mongo en DB Valuaciones.

## Qué hace
Valuación consolidada por cuenta precalculada en la base `Valuaciones`. Materializa el total valuado por cuenta para servir vistas de performance/actividad sin recomputar el portfolio en cada request.

Conecta con: la escribe el cron `jobs/consolidado_cuentas.py` (lee `SnapshotsCierre`); la leen `api/services/valuaciones.py` y `jobs/actividad_mensual.py`. Referenciada como singleton-friendly en `core/mongo.py`.

## Lo usan (backlinks) ←
- [[api.services.diagnostico_registry]]  ·  _module_
