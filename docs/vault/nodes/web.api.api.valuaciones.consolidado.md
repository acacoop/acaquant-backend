---
id: web.api.api.valuaciones.consolidado
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/valuaciones/consolidado/route.ts
---
2
# web /api/valuaciones/consolidado  (proxy)

**Archivo:** `src/app/api/valuaciones/consolidado/route.ts`

## Qué hace
Route handler que proxea la valuación consolidada por cuenta a /api/valuaciones/consolidado, con ?filtro_cuenta (default "todas"). force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/consolidado (api.routers.valuaciones, sobre Valuaciones.ConsolidadoCuentas); usado por la vista consolidada de patrimonio.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
