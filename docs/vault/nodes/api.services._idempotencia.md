---
id: api.services._idempotencia
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/_idempotencia.py
---

# api/services/_idempotencia

> Idempotencia de envío de órdenes — anti doble-orden (reintento / doble-click).

**Archivo:** `api/services/_idempotencia.py`

## Qué hace
Guard anti doble-orden. Cada intención de orden lleva un `client_order_id`: el primer envío reserva la clave y manda al broker; un reenvío con la MISMA clave no manda otra orden, devuelve el resultado del primero. Sin clave, no se invoca (retrocompatible). Degradación segura: ante error de infra prefiere MANDAR antes que tragar la orden.

Conecta con: usa `Operaciones.OrdenesIdempotency` (índice único en `key` para atomicidad ante doble submit simultáneo + TTL 1 día); lo invocan los routers operar/operativa al envolver el envío real (`ejecutar_idempotente`).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.operar]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
