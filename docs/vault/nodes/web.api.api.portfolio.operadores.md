---
id: web.api.api.portfolio.operadores
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\portfolio\operadores\route.ts
---

# web /api/portfolio/operadores  (proxy)

**Archivo:** `src\app\api\portfolio\operadores\route.ts`

## Qué hace
Route handler que trae la lista de operadores para el filtro madre de la vista AuM, desde /api/portfolio/operadores. force-dynamic + no-store.
- Conecta con: backend /api/portfolio/operadores (api.routers.carteras); consumido por el filtro de operador de la vista AuM.

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
