---
id: web.api.api.risk.[[...path]]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/risk/[[...path]]/route.ts
---

# web /api/risk/[[...path]]  (proxy)

**Archivo:** `src/app/api/risk/[[...path]]/route.ts`

## Qué hace
Proxy catch-all (GET/POST/DELETE) hacia /api/risk/* del backend — datos de cuenta del broker (saldos, posiciones, márgenes). Gateado por el módulo "operaciones" tanto en proxy.ts como en el backend; propaga identidad del usuario.
- Conecta con: backend /api/risk/* (api.routers.risk); usado por la vista de riesgo/cuenta del broker.

_Sin conexiones detectadas mecánicamente._
