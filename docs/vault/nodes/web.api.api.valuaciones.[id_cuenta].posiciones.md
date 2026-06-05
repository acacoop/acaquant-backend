---
id: web.api.api.valuaciones.[id_cuenta].posiciones
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\valuaciones\[id_cuenta]\posiciones\route.ts
---

# web /api/valuaciones/[id_cuenta]/posiciones  (proxy)

**Archivo:** `src\app\api\valuaciones\[id_cuenta]\posiciones\route.ts`

## Qué hace
Route handler que proxea las posiciones de una cuenta (valuadas a precio de mercado, con ?hasta opcional) a /api/valuaciones/{id_cuenta}/posiciones. Live (lee MarketSnapshot), force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/posiciones (api.routers.valuaciones); usado por la tabla de tenencia de la vista de valuaciones.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
