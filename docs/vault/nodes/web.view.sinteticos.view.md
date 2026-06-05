---
id: web.view.sinteticos.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/sinteticos/page.tsx
---

# web /sinteticos  (view)

**Archivo:** `src/app/sinteticos/page.tsx`

## Qué hace
Vista `/sinteticos` — sintéticos LECAP/DLK + futuro DLR (promovida desde Derivados a módulo top-level). Wrapper `force-dynamic` sin SSR fetch: la data se polleea client-side. Renderiza `DerivadosSinteticosView`.

Conecta con: componente `DerivadosSinteticosView` → backend `/api/derivados/sinteticos` (service `sinteticos`). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.derivados-sinteticos-view]]  ·  _component_
