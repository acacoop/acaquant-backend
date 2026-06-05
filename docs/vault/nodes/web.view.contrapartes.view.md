---
id: web.view.contrapartes.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\contrapartes\page.tsx
---

# web /contrapartes  (view)

**Archivo:** `src\app\contrapartes\page.tsx`

## Qué hace
Vista `/contrapartes` — gestión de contrapartes. Wrapper `force-dynamic` que delega en `ContrapartesShell`, que maneja state y fetches client-side.

Conecta con: componente `ContrapartesShell` → endpoints `/api/cuentas`/`/api/operaciones` (Contrapartes). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.contrapartes-shell]]  ·  _component_
