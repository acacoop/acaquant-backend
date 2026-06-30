---
id: web.view.agro.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/agro/page.tsx
---

# web /agro  (view)

**Archivo:** `src/app/agro/page.tsx`

## Qué hace
Vista `/agro` — derivados agro de Rosario (Trigo / Maíz / Soja). Hace SSR del tab "Mercado" (Pase Agro) pidiendo `/api/derivados/agro` y deja el resto de tabs (Mejoras Dispo, Datos) para cargar client-side. Renderiza `AgroShell` con la data inicial; usa `safeFetch` para no romper si el backend falla.

Conecta con: backend `GET /api/derivados/agro` (service `derivados_agro`); componente `AgroShell`.

## Usa / conecta con →
- [[web.cmp.agro-shell]]  ·  _component_
- [[web.lib.api]]  ·  _lib_
