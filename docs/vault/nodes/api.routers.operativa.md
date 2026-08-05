---
id: api.routers.operativa
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/operativa.py
---

# api/routers/operativa

> Router /api/operativa — wrappers operativos sobre /api/ordenes.

**Archivo:** `api/routers/operativa.py`

## Qué hace
Router de "operativas" — wrappers de alto nivel sobre /api/ordenes que empaquetan una operación de mesa en sus 2 órdenes atómicas. Hoy solo dólar MEP: compra (BUY AL30 + SELL AL30D) y venta (camino inverso USD→ARS), más cotizaciones live, serie MEP por minuto y listado/detalle de operativas del día. Pensado para crecer con CCL, canjes, etc.

Conecta con: delega en `api.services.operativa_mep`; aplica scope de grupos + idempotencia (`client_order_id`); hereda RBAC del módulo `operaciones`; lo consume la tab MEP del frontend.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.operativa_mep_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
