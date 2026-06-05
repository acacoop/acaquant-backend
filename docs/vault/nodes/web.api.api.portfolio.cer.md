---
id: web.api.api.portfolio.cer
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\portfolio\cer\route.ts
---

# web /api/portfolio/cer  (proxy)

**Archivo:** `src\app\api\portfolio\cer\route.ts`

## Qué hace
Route handler que proxea la tenencia de bonos CER consolidada a /api/portfolio/cer, con filtro opcional ?operador. Sin cache.
- Conecta con: backend /api/portfolio/cer (api.routers.carteras / api.services.portfolio); usado por la pestaña CER de la vista AuM.

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
