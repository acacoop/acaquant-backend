---
id: web.api.api.cashflow
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/cashflow/route.ts
---

# web /api/cashflow  (proxy)

**Archivo:** `src/app/api/cashflow/route.ts`

## Qué hace
Arma la vista de cashflow: en paralelo pide los flujos de los últimos ~2 años (`/api/operaciones/flujos`) y el padrón de accionistas (`/api/cuentas/accionistas`), y los devuelve juntos. Como trae PII de clientes, fuerza `Cache-Control: private, no-store` (el backend ya cachea 300s para ahorrar Mongo).

Conecta con: vista de cashflow del front → este route → backend `api/routers/operaciones.py` + `api/routers/cuentas.py`.

## Usa / conecta con →
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
