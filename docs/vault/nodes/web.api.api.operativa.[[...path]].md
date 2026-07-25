---
id: web.api.api.operativa.[[...path]]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/operativa/[[...path]]/route.ts
---

# web /api/operativa/[[...path]]  (proxy)

**Archivo:** `src/app/api/operativa/[[...path]]/route.ts`

## Qué hace
Proxy catch-all (GET/POST/DELETE) hacia /api/operativa/* del backend — wrappers operativos sobre órdenes (ej. operativa MEP). Reenvía body, auth de service token e identidad del usuario para el audit.
- Conecta con: backend /api/operativa/* (api.routers.operativa); usado por los botones de operativa rápida del frontend.

_Sin conexiones detectadas mecánicamente._
