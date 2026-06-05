---
id: web.api.api.operar.[[...path]]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/operar/[[...path]]/route.ts
---

# web /api/operar/[[...path]]  (proxy)

**Archivo:** `src/app/api/operar/[[...path]]/route.ts`

## Qué hace
Proxy catch-all (GET/POST/DELETE) hacia /api/operar/* del backend, soporte de la vista Operar Dashboard. Reenvía body JSON, auth de service token e identidad del usuario.
- Conecta con: backend /api/operar/* (api.routers.operar); usado por el dashboard de operación.

_Sin conexiones detectadas mecánicamente._
