---
id: web.lib.use-persisted-state
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/use-persisted-state.ts
---

# web/lib/use-persisted-state

**Archivo:** `src/lib/use-persisted-state.ts`

## Qué hace
Hook `usePersistedState` — un `useState` que sobrevive a la navegación entre rutas y a un F5 durante la sesión del tab, respaldado en `sessionStorage`. Resuelve que el App Router desmonta la vista al navegar y pierde los filtros/selección; con esto el usuario retoma donde dejó. Usa `sessionStorage` (no `localStorage`) a propósito: arranca limpio al reabrir la app. SSR-safe (primer render con `initial`, rehidrata al montar). Solo para estado serializable a JSON y que sea elección del usuario, no data fetcheada.

Conecta con: hook de cliente puro, sin red ni Mongo; lo usan las vistas del frontend con filtros persistentes (ej. clave `"ops.moneda"`).

## Lo usan (backlinks) ←
- [[web.cmp.coberturas-view]]  ·  _component_
- [[web.cmp.comercial-operaciones-view]]  ·  _component_
- [[web.cmp.manager-view]]  ·  _component_
- [[web.cmp.operaciones-view]]  ·  _component_
- [[web.cmp.operadores-view]]  ·  _component_
- [[web.cmp.ops-view]]  ·  _component_
- [[web.cmp.referidos-view]]  ·  _component_
- [[web.cmp.retorno-total-view]]  ·  _component_
- [[web.cmp.tenencia-valorizada-view]]  ·  _component_
