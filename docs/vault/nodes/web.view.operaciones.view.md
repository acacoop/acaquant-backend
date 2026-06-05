---
id: web.view.operaciones.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/operaciones/page.tsx
---

# web /operaciones  (view)

**Archivo:** `src/app/operaciones/page.tsx`

## Qué hace
Vista `/operaciones` — flujo de negocio / boletos (movimientos consolidados). Wrapper `force-dynamic` que delega en `OperacionesView`, que hace sus fetches client-side.

Conecta con: componente `OperacionesView` → backend `/api/operaciones` (CashFlow.NegocioMovimientos / Operaciones). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.operaciones-view]]  ·  _component_
