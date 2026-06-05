---
id: web.api.api.market.calendar
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\market\calendar\route.ts
---

# web /api/market/calendar  (proxy)

**Archivo:** `src\app\api\market\calendar\route.ts`

## Qué hace
Route handler liviano que proxea el calendario económico global al backend (/api/market/calendar/economic), reenviando el querystring tal cual. Sin cache (revalidate 0).
- Conecta con: backend /api/market/calendar/economic (api.routers.market); consumido por la vista de calendario económico del frontend.

## Usa / conecta con →
- [[api.routers.market]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
