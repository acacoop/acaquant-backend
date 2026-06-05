---
id: core.rofex_orders_session
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/rofex_orders_session.py
---

# core/rofex_orders_session

> Sesión pyRofex dedicada a envío/seguimiento de órdenes.

**Archivo:** `core/rofex_orders_session.py`

## Qué hace
Sesión pyRofex dedicada exclusivamente a enviar y seguir órdenes, separada de la de market data para que no se pisen (pyRofex es singleton por proceso). Dos modos según la env var `ROFEX_ORDERS_ENV`: REMARKET (sandbox 24/7) o LIVE (ACA Valores). Ofrece `inicializar_para_envio()` (REST-only, para que la API mande/cancele órdenes), `ensure_session_envio()` (idempotente y thread-safe) e `inicializar_para_motor()` (abre WS + suscribe a order reports para el motor de órdenes). Inicialización con retry y backoff.

Conecta con: la plataforma ROFEX/Primary vía pyRofex; lee credenciales `ROFEX_*` del `.env`. Lo usan `api.services.ordenes`/`api.services.risk` (REST) y `engines.motor_ordenes` (WS → `Operaciones.OrdenesLive`/`OrdenesAudit`).

## Lo usan (backlinks) ←
- [[api.services.ordenes]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
