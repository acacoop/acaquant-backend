---
id: web.cmp.header
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/header.tsx
---

# web/components/header

**Archivo:** `src/components/header.tsx`

## Qué hace
Barra de navegación global de la app. Arma el menú (links sueltos HOME/OPERAR/CARTERAS/BACK OFFICE/MANAGER + dropdowns MERCADOS y NEGOCIO) y gatea cada vista por su `module`, alineado con la matriz de roles del backend (`core/roles.py::MODULES`). Un grupo solo aparece si el usuario tiene al menos una vista adentro.

Conecta con: consume los módulos permitidos del usuario (vía `/api/me` / RBAC); si modules es null (dev o backend caído) muestra todo. Se renderiza en el layout raíz de todas las páginas.

## Usa / conecta con →
- [[web.cmp.ia-vista-panel]]  ·  _component_
- [[web.lib.use-is-guest]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.(home).layout]]  ·  _view_
