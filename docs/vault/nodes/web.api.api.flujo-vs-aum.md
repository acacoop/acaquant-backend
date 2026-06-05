---
id: web.api.api.flujo-vs-aum
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\flujo-vs-aum\route.ts
---

# web /api/flujo-vs-aum  (proxy)

**Archivo:** `src\app\api\flujo-vs-aum\route.ts`

## Qué hace
Endpoint dual de la vista Flujo vs AuM. Sin `contraparte` devuelve la lista de fondos/contrapartes (`/api/operaciones/fondos`); con `contraparte` (+ `moneda`, default ARS) devuelve las dos series mensuales a comparar: AuM por mes y flujo bruto por mes. Cachea con `s-maxage=300` + stale-while-revalidate.

Conecta con: vista Flujo vs AuM del front → este route → backend `api/routers/operaciones.py` (`/api/operaciones/fondos`, `/api/operaciones/flujo-vs-aum`).

## Usa / conecta con →
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
