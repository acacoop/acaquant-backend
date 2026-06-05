---
id: web.cmp.theme-toggle
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/theme-toggle.tsx
---

# web/components/theme-toggle

**Archivo:** `src/components/theme-toggle.tsx`

## Qué hace
Switch de tema claro/oscuro. Alterna la clase "light" en el elemento html y guarda la preferencia en localStorage; el default es claro y el oscuro es opt-in. El anti-parpadeo en la carga inicial lo resuelve un script en el layout.

Conecta con: 100% cliente, sin API. Sincroniza con el script anti-flash del layout raíz y con las variables CSS de tema.

## Lo usan (backlinks) ←
- [[web.view.(home).layout]]  ·  _view_
