---
id: web.api.api.trades
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\trades\route.ts
---

# web /api/trades  (proxy)

**Archivo:** `src\app\api\trades\route.ts`

## Qué hace
Route handler que proxea el time & sales intradía de un instrumento: recibe ?instrumento, valida y pega a /api/cotizaciones/historico/trades. Sin cache — el panel del libro hace polling propio cada 5s.
- Conecta con: backend /api/cotizaciones/historico/trades (api.routers.cotizaciones, sobre Trading.TimeSales); usado por el LibroPanel del frontend.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
