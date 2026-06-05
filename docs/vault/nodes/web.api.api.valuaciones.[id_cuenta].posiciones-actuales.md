---
id: web.api.api.valuaciones.[id_cuenta].posiciones-actuales
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/valuaciones/[id_cuenta]/posiciones-actuales/route.ts
---

# web /api/valuaciones/[id_cuenta]/posiciones-actuales  (proxy)

**Archivo:** `src/app/api/valuaciones/[id_cuenta]/posiciones-actuales/route.ts`

## Qué hace
Route handler que proxea las posiciones actuales de una cuenta a una fecha (?fecha opcional) a /api/valuaciones/{id_cuenta}/posiciones-actuales. force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/posiciones-actuales (api.routers.valuaciones); usado por la vista de tenencia puntual por cuenta.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
