---
id: web.api.api.market.quotes
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\market\quotes\route.ts
---

# web /api/market/quotes  (proxy)

**Archivo:** `src\app\api\market\quotes\route.ts`

## Qué hace
Route handler que proxea las cotizaciones de watchlists (equity + forex) al backend (/api/market/quotes), pasando el querystring. Sin cache.
- Conecta con: backend /api/market/quotes (api.routers.market, alimentado por jobs.market_quotes); usado por los widgets de cotizaciones del frontend.

## Usa / conecta con →
- [[api.routers.market]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
