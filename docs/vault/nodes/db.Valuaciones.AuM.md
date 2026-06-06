---
id: db.Valuaciones.AuM
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Valuaciones.AuM

> Colección Mongo en DB Valuaciones.

## Qué hace
Snapshot diario de tenencias valuadas por cuenta (Assets under Management) en la base `Valuaciones`. Aplica las fórmulas de valuación por tipo de activo (renta fija `cant×precio/100`, FCI/OTROS `cant×precio`, futuros `(precio+1)×cant`) tras filtros de exclusión.

Conecta con: la escribe el cron `jobs/aum.py` (23 UTC L-V) con reglas de `jobs/_aum_filters.py`; la leen `api/services/portfolio.py`, `comercial.py`, `_mep.py` y routers de valuaciones. Backfill con `jobs/aum_backfill*.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_
- [[jobs.aum]]  ·  _module_
- [[jobs.aum_backfill]]  ·  _module_
- [[jobs.aum_backfill_historico]]  ·  _module_
- [[jobs.aum_resumen_fci]]  ·  _module_
