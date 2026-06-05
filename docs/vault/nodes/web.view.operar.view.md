---
id: web.view.operar.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\operar\page.tsx
---

# web /operar  (view)

**Archivo:** `src\app\operar\page.tsx`

## Qué hace
Vista `/operar` — dashboard de trading (envío de órdenes, order book live, riesgo de cuenta). Wrapper `force-dynamic` que delega en `OperarShell`.

Conecta con: componente `OperarShell` → backend `/api/operar`, `/api/ordenes`, `/api/risk`. Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.operar-shell]]  ·  _component_
