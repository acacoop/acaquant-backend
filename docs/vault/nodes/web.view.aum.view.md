---
id: web.view.aum.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\aum\page.tsx
---

# web /aum  (view)

**Archivo:** `src\app\aum\page.tsx`

## Qué hace
Vista `/aum` — Activos bajo Administración (AuM). Wrapper `force-dynamic` que delega todo al componente `AumView`, que hace sus propios fetches client-side.

Conecta con: componente `AumView` → endpoints de `/api/carteras`/`/api/valuaciones` (AuM). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.aum-view]]  ·  _component_
