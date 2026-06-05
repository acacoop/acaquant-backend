---
id: web.api.api.manager.[...path]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/manager/[...path]/route.ts
---

# web /api/manager/[...path]  (proxy)

**Archivo:** `src/app/api/manager/[...path]/route.ts`

## Qué hace
Proxy genérico (catch-all) de todo el módulo Manager hacia el backend FastAPI: reenvía GET/POST/PATCH/PUT/DELETE y JSON o multipart. Adjunta auth (Bearer + CF-Access service token) y propaga la identidad del usuario en cf-access-authenticated-user-email + x-acaquant-user-email para que el backend loguee el actor real en Manager.RoleAudit.
- Conecta con: backend /api/manager/* (api.routers.manager); usado por toda la UI de administración (usuarios, roles, grupos, jobs, clientes, assets).

_Sin conexiones detectadas mecánicamente._
