---
id: web.view.retorno.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\retorno\page.tsx
---

# web /retorno  (view)

**Archivo:** `src\app\retorno\page.tsx`

## Qué hace
Vista `/retorno` — retorno total / comparar inversión de bonos. Wrapper mínimo (sin `force-dynamic` ni SSR fetch) que delega todo en `RetornoTotalView`, que hace sus fetches client-side.

Conecta con: componente `RetornoTotalView` → backend `/api/analitica` y `/api/cotizaciones` (descomposición de retorno, comparar inversión, carry trade). Vive bajo el layout raíz.

## Usa / conecta con →
- [[web.cmp.retorno-total-view]]  ·  _component_
