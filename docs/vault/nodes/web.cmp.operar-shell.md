---
id: web.cmp.operar-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\operar-shell.tsx
---

# web/components/operar-shell

**Archivo:** `src\components\operar-shell.tsx`

## Qué hace
Shell de la vista /operar. Tres tabs: DASHBOARD (trading manual), FCI y DOLAR MEP. Soporta deep-link (`?tab=`) para abrir directo la pestaña correcta desde Valuaciones.

Conecta con: no hace fetch propio — monta `OperarDashboardView`, `OperarFciView` y `DolarMepShell`. Gateada por el módulo `operar` en `header`.

## Lo usan (backlinks) ←
- [[web.view.operar.view]]  ·  _view_
