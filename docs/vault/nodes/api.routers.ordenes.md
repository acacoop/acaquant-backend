---
id: api.routers.ordenes
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\ordenes.py
---

# api/routers/ordenes

> Router /api/ordenes — envío/cancel/listado de órdenes contra ROFEX (LIVE).

**Archivo:** `api\routers\ordenes.py`

## Qué hace
Router de órdenes LIVE contra ROFEX. `POST /api/ordenes` envía (LIMIT/MARKET, BUY/SELL, DAY/IOC/FOK/GTC), `DELETE /{id}` cancela, `GET /dia` y `GET /{id}` listan/consultan estado. Thin wrapper: toda la lógica vive en el service. La sesión pyRofex es lazy (se inicializa en el primer envío dentro del proceso uvicorn).

Conecta con: delega en `api.services.ordenes` (que habla con `core.rofex_orders_session`); gate RBAC módulo `operar` + scope de grupos por cuenta (`verificar_account`); registra el actor (email) en audit log; el estado de cada orden lo mantiene `engines.motor_ordenes`.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.ratelimit]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.ordenes_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
