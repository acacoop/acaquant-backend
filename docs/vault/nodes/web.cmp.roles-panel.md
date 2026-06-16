---
id: web.cmp.roles-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\roles-panel.tsx
---

# web/components/roles-panel

**Archivo:** `src\components\roles-panel.tsx`

## Qué hace
Panel del Manager para editar la matriz de roles × módulos: marca/desmarca qué módulos puede ver cada rol y guarda los cambios. Muestra además el audit log (últimas 50 entradas: quién cambió qué y cuándo).

Conecta con: GET/PUT /api/manager/roles y GET /api/manager/roles/audit (api.routers.manager.roles), que persisten en Manager.RoleMatrix y registran en Manager.RoleAudit.

## Usa / conecta con →
- [[api.routers.manager.roles]]  ·  _module_
