---
id: web.api.api.ordenes.[[...path]]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\ordenes\[[...path]]\route.ts
---

# web /api/ordenes/[[...path]]  (proxy)

**Archivo:** `src\app\api\ordenes\[[...path]]\route.ts`

## Qué hace
Proxy catch-all (GET/POST/DELETE) hacia /api/ordenes/* del backend — envío, cancelación y listado de órdenes contra ROFEX. Propaga la identidad del usuario para que el audit registre quién mandó/canceló cada orden.
- Conecta con: backend /api/ordenes/* (api.routers.ordenes, sesión pyRofex de órdenes); usado por la UI de trading.

_Sin conexiones detectadas mecánicamente._
