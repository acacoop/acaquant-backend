---
id: db.CashFlow.Operaciones
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CashFlow.Operaciones

> Colección Mongo en DB CashFlow.

## Qué hace
Operaciones normalizadas e ingestadas desde los informes de Aunesa, en la base `CashFlow`. Es el registro canónico de operaciones para análisis y backfill (carga por CSV o por job).

Conecta con: la escriben `jobs/operaciones_informes.py` y `api/services/operaciones_informes.py` (normalización + ingesta idempotente); backfill manual por CSV vía `api/routers/manager/operaciones.py`. La leen routers de operaciones/operativa y el scoping de grupos (`_grupos_scope.py`).

## Lo usan (backlinks) ←
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[api.services.operaciones_view]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[jobs.descubrir_cuentas]]  ·  _module_
- [[jobs.informe_salud]]  ·  _module_
