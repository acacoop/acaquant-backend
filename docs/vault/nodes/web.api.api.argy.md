---
id: web.api.api.argy
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\argy\route.ts
---

# web /api/argy  (proxy)

**Archivo:** `src\app\api\argy\route.ts`

## Qué hace
Endpoint del panel ARGY (watchlist de métricas argentinas live). Pega a `/api/cotizaciones/argy` del backend y devuelve el JSON con `Cache-Control: no-store` — deliberadamente sin cache de edge para que el poll cada 5s del cliente siempre llegue al origin (el backend ya tiene su `@cached(ttl=5)`).

Conecta con: vista ARGY del front → este route → backend `api/routers/cotizaciones.py` (servicio `argy`).

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
