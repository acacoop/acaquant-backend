---
id: web.api.api.dolares-historico
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\dolares-historico\route.ts
---

# web /api/dolares-historico  (proxy)

**Archivo:** `src\app\api\dolares-historico\route.ts`

## Qué hace
Proxy del histórico de dólares (MEP + CCL + oficial) que alimenta el chart custom del panel ARGY: propaga la querystring a `/api/cotizaciones/historico/dolares` y devuelve sin cache (la cadencia la decide el polling del cliente).

Conecta con: chart de dólares del panel ARGY en el front → este route → backend `GET /api/cotizaciones/historico/dolares` (service `api/services/argy.py`).

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
