---
id: web.api.api.valuaciones.[id_cuenta].serie
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\valuaciones\[id_cuenta]\serie\route.ts
---

# web /api/valuaciones/[id_cuenta]/serie  (proxy)

**Archivo:** `src\app\api\valuaciones\[id_cuenta]\serie\route.ts`

## Qué hace
Route handler que proxea la serie temporal de valuación de una cuenta (rango ?desde/?hasta opcional) a /api/valuaciones/{id_cuenta}/serie. force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/serie (api.routers.valuaciones); usado por el gráfico de evolución patrimonial de la cuenta.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
