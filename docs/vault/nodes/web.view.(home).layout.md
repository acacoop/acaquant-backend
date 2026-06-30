---
id: web.view.(home).layout
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/layout.tsx
---

# web /(home)  (layout)

**Archivo:** `src/app/layout.tsx`

## Qué hace
Layout raíz de la app Next.js (acaquant-web): define el `<html>`, la fuente JetBrains Mono self-hosted, el tema claro/oscuro anti-parpadeo, y arma el chrome común (Header con nav, PauseBanner, footer). Es `force-dynamic` por RBAC: re-renderea por usuario para no servir el HTML de un admin (con MANAGER en el nav) a un trader.

- Llama a `getMe()` y pasa `me.modules` al Header; fail-closed en prod (modules=[] = solo públicos si el backend no responde).

Conecta con: `getMe()` → backend `/api/me`; componentes `Header`, `PauseBanner`, `ThemeToggle`. Envuelve todas las views.

## Usa / conecta con →
- [[web.cmp.header]]  ·  _component_
- [[web.cmp.theme-toggle]]  ·  _component_
- [[web.lib.me]]  ·  _lib_
