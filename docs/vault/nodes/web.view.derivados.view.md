---
id: web.view.derivados.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/derivados/page.tsx
---

# web /derivados  (view)

**Archivo:** `src/app/derivados/page.tsx`

## Qué hace
Vista `/derivados` — ahora SOLO Opciones (Agro y Sintéticos se promovieron a `/agro` y `/sinteticos`). SSR de la meta de opciones (`/api/cotizaciones/opciones/meta`: tasa, VR local/ADR) en paralelo con `getMe()`; la chain de opciones se polleea client-side al montar. Pasa `isAdmin` al shell para habilitar el editor de tasa.

Conecta con: backend `GET /api/cotizaciones/opciones/meta`, `getMe()`; componente `DerivadosShell`.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.cmp.derivados-shell]]  ·  _component_
- [[web.lib.api]]  ·  _lib_
- [[web.lib.estrategias]]  ·  _lib_
- [[web.lib.me]]  ·  _lib_
