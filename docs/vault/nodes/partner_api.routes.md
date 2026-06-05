---
id: partner_api.routes
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api\routes.py
---

# partner_api/routes

> Endpoints de datos del partner_api — SOLO lectura de ACAPortfolio.Cartera.

**Archivo:** `partner_api\routes.py`

## Qué hace
Endpoints de datos del servicio, todos de solo lectura y con Bearer token obligatorio. `GET /v1/fechas` devuelve las fechas disponibles en el export (más reciente primero); `GET /v1/portfolio` devuelve las posiciones por (cuenta, instrumento) con cantidad/precio/valuación, filtrables por fecha e `id_cuenta`. Oculta campos internos (`_id`, `exported_at`) y, como la colección solo trae cuentas habilitadas, no se puede pedir una que no esté.

Conecta con: lee `ACAPortfolio.Cartera` vía `partner_api.db.get_db`; protegido por `Depends(usuario_actual)` de `partner_api.auth` y limitado por `partner_api.ratelimit`. Montado por `partner_api.main`.

## Usa / conecta con →
- [[db.ACAPortfolio.Cartera]]  ·  _collection_
- [[partner_api.auth]]  ·  _module_
- [[partner_api.db]]  ·  _module_
- [[partner_api.ratelimit]]  ·  _module_

## Lo usan (backlinks) ←
- [[partner_api.main]]  ·  _module_
