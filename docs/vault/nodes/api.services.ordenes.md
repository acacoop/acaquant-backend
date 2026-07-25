---
id: api.services.ordenes
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/ordenes.py
---

# api/services/ordenes

> Servicio de órdenes — funciones puras invocables desde routers o scripts.

**Archivo:** `api/services/ordenes.py`

## Qué hace
Servicio de envío/cancelación/listado de órdenes contra ROFEX. Funciones puras (sin FastAPI) para que las pueda usar un router o un script. Persiste el doc inicial (PENDING_NEW) y el audit del request en Mongo ANTES de tocar al broker, así toda orden queda trackeable aunque pyRofex falle; el estado real (NEW/REJECTED) lo upsertea después el motor de órdenes cuando llega el execution report.

Conecta con: usa la sesión REST liviana de `core.rofex_orders_session` (compartida con `risk`/`operativa_mep`); escribe en `Operaciones.OrdenesLive` y `Operaciones.OrdenesAudit`. Lo invocan el router `/api/ordenes` y `operativa_mep`.

## Usa / conecta con →
- [[api.services._idempotencia]]  ·  _module_
- [[core]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[core.rofex_orders_session]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.operar]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.ordenes_sql]]  ·  _module_
