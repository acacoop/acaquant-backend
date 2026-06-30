---
id: web.view.manager.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/manager/page.tsx
---

# web /manager  (view)

**Archivo:** `src/app/manager/page.tsx`

## Qué hace
Vista `/manager` — panel de administración (usuarios, roles, grupos, comercial, clientes, jobs, logs). Server component que lee `me.modules` y aplica RBAC: si el usuario no tiene `manager` ni ningún sub-módulo (`manager_comercial`, `manager_clientes`...) devuelve 404 (defense in depth contra acceso por URL directa). Pasa `modules` a `ManagerView` para que filtre las tabs.

Conecta con: `getMe()` → backend `/api/me`; componente `ManagerView` → routers `/api/manager/*`.

## Usa / conecta con →
- [[web.cmp.manager-view]]  ·  _component_
- [[web.lib.me]]  ·  _lib_
