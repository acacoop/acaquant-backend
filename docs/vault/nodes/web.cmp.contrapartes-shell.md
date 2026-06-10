---
id: web.cmp.contrapartes-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/contrapartes-shell.tsx
---

# web/components/contrapartes-shell

**Archivo:** `src/components/contrapartes-shell.tsx`

## Qué hace
Contenedor de la vista /contrapartes que junta en dos tabs lo que antes eran vistas sueltas de /operaciones: CONTRAPARTES (flujo por contraparte) y FLUJO vs AUM. Solo arma la navegación entre ambas.

Conecta con: monta `contrapartes-view` y `flujo-vs-aum-view`; esas vistas consumen los endpoints de operaciones sobre `CashFlow.Contrapartes` y `Valuaciones.AuM`.

## Lo usan (backlinks) ←
- [[web.view.contrapartes.view]]  ·  _view_
