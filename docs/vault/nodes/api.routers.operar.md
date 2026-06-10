---
id: api.routers.operar
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/operar.py
---

# api/routers/operar

> Router /api/operar — soporte para la vista "Operar Dashboard".

**Archivo:** `api/routers/operar.py`

## Qué hace
Router de soporte para la vista "Operar Dashboard". `GET /api/operar/order-book?ticker=X` devuelve el top-5 bid/ask de cualquier ticker; si el motor no lo suscribe todavía, lo registra en AdhocSubscriptions y responde 202 (lo levanta al próximo poll, ~5s). `POST /bracket` manda una orden LIMIT de entrada y persiste un bracket cuya salida automática dispara el motor_ordenes al FILLED. `GET /brackets/dia` lista los brackets del día.

Conecta con: usa `order_book`, `ordenes.send_order`, `core.brackets`, `core.adhoc_subscriptions`; scope de grupos + idempotencia; gate RBAC admin-only (`operar`); lo consume el Operar Dashboard del frontend.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[core.adhoc_subscriptions]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.cmp.derivados-operar]]  ·  _component_
- [[web.cmp.operar-dashboard-view]]  ·  _component_
- [[web.lib.proxy]]  ·  _lib_
