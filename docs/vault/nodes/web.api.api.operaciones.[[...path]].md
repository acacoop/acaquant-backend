---
id: web.api.api.operaciones.[[...path]]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/operaciones/[[...path]]/route.ts
---

# web /api/operaciones/[[...path]]  (proxy)

**Archivo:** `src/app/api/operaciones/[[...path]]/route.ts`

## Qué hace
Proxy catch-all read-only (solo GET) hacia /api/operaciones/* del backend. Adjunta auth de service token y propaga la identidad del usuario para el RBAC/scoping. force-dynamic + no-store.
- Conecta con: backend /api/operaciones/* (api.routers.operaciones, sobre CashFlow.NegocioMovimientos/Operaciones); consumido por la vista Operaciones / Negocio.

_Sin conexiones detectadas mecánicamente._
