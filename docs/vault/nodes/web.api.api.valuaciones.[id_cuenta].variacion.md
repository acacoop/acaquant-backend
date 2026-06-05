---
id: web.api.api.valuaciones.[id_cuenta].variacion
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/valuaciones/[id_cuenta]/variacion/route.ts
---

# web /api/valuaciones/[id_cuenta]/variacion  (proxy)

**Archivo:** `src/app/api/valuaciones/[id_cuenta]/variacion/route.ts`

## Qué hace
Route handler que proxea la variación de una cuenta para una fecha (?fecha requerido) a /api/valuaciones/{id_cuenta}/variacion. force-dynamic + no-store.
- Conecta con: backend /api/valuaciones/{id}/variacion (api.routers.valuaciones); usado para mostrar la variación diaria del patrimonio.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
