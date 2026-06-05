---
id: web.view.valuaciones.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\valuaciones\page.tsx
---

# web /valuaciones  (view)

**Archivo:** `src\app\valuaciones\page.tsx`

## Qué hace
Vista `/valuaciones` — performance e historia por cuenta. Wrapper `force-dynamic` que delega en `ValuacionesShell`, donde vive todo el state (cuenta seleccionada, sub-tab, fetches).

Conecta con: componente `ValuacionesShell` → backend `/api/valuaciones` (service `valuaciones`, XIRR/PnL por cuenta). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.valuaciones-shell]]  ·  _component_
