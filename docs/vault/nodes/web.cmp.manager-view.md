---
id: web.cmp.manager-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\manager-view.tsx
---

# web/components/manager-view

**Archivo:** `src\components\manager-view.tsx`

## Qué hace
Vista contenedora del módulo Manager (/manager, admin-only). Organiza en tabs todos los paneles de administración: usuarios, roles, grupos, jobs, logs, recursos, comercial, exploradores Aunesa y los paneles de debug (XIRR, segmento, comercial, curva). Usa imports estáticos para que cambiar de tab sea instantáneo.

Conecta con: no hace fetch propio relevante — orquesta los sub-paneles, cada uno con su endpoint `/api/manager/*`. Gateada por el módulo `manager` en `header`.

## Usa / conecta con →
- [[api.routers.manager]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.compliance]]  ·  _module_
- [[api.routers.manager.diagnostico]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.options]]  ·  _module_
- [[web.lib.use-persisted-state]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.manager.view]]  ·  _view_
