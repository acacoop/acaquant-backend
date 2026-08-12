---
id: api.routers.operaciones
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/operaciones.py
---

# api/routers/operaciones

> Router Operaciones: endpoints para MesaAPI (flujo contrapartes), FlujosAPI

**Archivo:** `api/routers/operaciones.py`

## Qué hace
Router de la vista Operaciones (back-office de la mesa). Sirve el flujo de contrapartes (MesaAPI), los movimientos (FlujosAPI) y la "vista de negocio del día" sobre NegocioMovimientos, con filtros por contraparte/moneda/segmento y por tipo de cuenta. Aplica scope de grupos para que cada usuario vea solo sus cuentas y excluye futuros del agregado de negocio.

Conecta con: lee `CashFlow.NegocioMovimientos` + colecciones `*API` (Mesa/Flujos); usa `_cuentas_filter`, `_grupos_scope` y `_negocio_futuros`; gate RBAC módulo `operaciones`; lo consume la vista /operaciones del frontend.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services.cashflow_sql]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comercial_sql]]  ·  _module_
- [[api.services.control_comercial_sql]]  ·  _module_
- [[api.services.financiamiento]]  ·  _module_
- [[api.services.financiamiento_calc]]  ·  _module_
- [[api.services.intraday]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.operaciones_view]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
