---
id: web.api.api.mep
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\mep\route.ts
---

# web /api/mep  (proxy)

**Archivo:** `src\app\api\mep\route.ts`

## Qué hace
Route handler que proxea el dólar MEP live al backend (/api/cotizaciones/mep). Forzado a no-store: el MEP viene del motor de dólares con snapshot de 5s y el edge cache de 30s dejaba el indicador desactualizado.
- Conecta con: backend /api/cotizaciones/mep (api.routers.cotizaciones, alimentado por engines.dolares); usado por el indicador del top ticker.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
