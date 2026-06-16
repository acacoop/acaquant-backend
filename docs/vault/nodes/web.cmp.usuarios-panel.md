---
id: web.cmp.usuarios-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\usuarios-panel.tsx
---

# web/components/usuarios-panel

**Archivo:** `src\components\usuarios-panel.tsx`

## Qué hace
Panel del Manager para administrar usuarios: lista (email, rol, habilitado, notas, alta/última actividad), marca con un umbral de inactividad (90 días) para la revisión de accesos, y permite crear usuarios, cambiar su rol, habilitarlos/deshabilitarlos y editar notas.

Conecta con: GET/POST/PATCH /api/manager/users (api.routers.manager.users), que persiste en Manager.Users. Solo accesible para el rol Manager.

## Usa / conecta con →
- [[api.routers.manager.users]]  ·  _module_
