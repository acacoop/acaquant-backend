---
id: web.api.api.valuaciones.[id_cuenta].movimientos
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\valuaciones\[id_cuenta]\movimientos\route.ts
---

# web /api/valuaciones/[id_cuenta]/movimientos  (proxy)

**Archivo:** `src\app\api\valuaciones\[id_cuenta]\movimientos\route.ts`

## Qué hace
Route handler que proxea los movimientos de una cuenta para una fecha dada (?fecha requerido) a /api/valuaciones/{id_cuenta}/movimientos. force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/movimientos (api.routers.valuaciones); usado al expandir el detalle de un día en la vista de valuaciones.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
