---
id: web.api.api.valuaciones.[id_cuenta].mensual
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/valuaciones/[id_cuenta]/mensual/route.ts
---

# web /api/valuaciones/[id_cuenta]/mensual  (proxy)

**Archivo:** `src/app/api/valuaciones/[id_cuenta]/mensual/route.ts`

## Qué hace
Route handler que proxea la performance mensual (XIRR) de una cuenta a /api/valuaciones/{id_cuenta}/mensual. force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/mensual (api.routers.valuaciones / api.services.valuaciones); usado por la vista de rendimiento mensual por cuenta.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
