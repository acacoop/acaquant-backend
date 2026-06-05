---
id: web.api.api.caucion
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/caucion/route.ts
---

# web /api/caucion  (proxy)

**Archivo:** `src/app/api/caucion/route.ts`

## Qué hace
Proxy live del mercado de caución (repo): reenvía el parámetro opcional `moneda` (ARS/USD) a `/api/cotizaciones/caucion` y devuelve sin cache, porque el motor de caución snapshotea cada 5s y el edge cache pisaba el polling.

Conecta con: vista de caución del front → este route → backend `GET /api/cotizaciones/caucion` (service `api/services/repo.py`, motor `engines/caucion.py`).

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
