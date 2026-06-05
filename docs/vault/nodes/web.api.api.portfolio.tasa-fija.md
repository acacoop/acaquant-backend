---
id: web.api.api.portfolio.tasa-fija
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\portfolio\tasa-fija\route.ts
---

# web /api/portfolio/tasa-fija  (proxy)

**Archivo:** `src\app\api\portfolio\tasa-fija\route.ts`

## Qué hace
Route handler que proxea la tenencia de bonos de tasa fija consolidada a /api/portfolio/tasa-fija, con filtro opcional ?operador. Sin cache.
- Conecta con: backend /api/portfolio/tasa-fija (api.routers.carteras / api.services.portfolio); usado por la pestaña tasa fija de la vista AuM.

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
