---
id: web.cmp.operadores-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/operadores-view.tsx
---

# web/components/operadores-view

**Archivo:** `src/components/operadores-view.tsx`

## Qué hace
Vista /operadores (ex tab Comercial). Barra superior con selector de operador + moneda (ARS/USD); si tu email está registrado como operador arrancás viendo el tuyo, si no, el primero de la lista. Pasa la selección a la vista comercial de operaciones.

Conecta con: fetch a `/api/operaciones/comercial/operadores` y `/api/me` (identidad). Renderiza `ComercialOperacionesView`. Gateada por el módulo `operaciones`.

## Usa / conecta con →
- [[api.routers.me]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.use-persisted-state]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.operadores.view]]  ·  _view_
