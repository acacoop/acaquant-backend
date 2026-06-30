---
id: web.lib.types
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/types.ts
---

# web/lib/types

**Archivo:** `src/lib/types.ts`

## Qué hace
Catálogo de tipos TypeScript compartidos entre páginas y componentes del frontend, centralizando contratos que antes estaban duplicados en cada archivo. Define las formas de los documentos que devuelve el backend: renta fija (`RentaFijaDoc` con métricas, TC breakeven, flujo de vencimiento), forwards (matriz, histórico y z-scores), fair value, entre otros. Es la fuente de verdad de los shapes para que el frontend tipee las respuestas de la API.

Conecta con: refleja los modelos que serializan los services del backend (`api.services.renta_fija`, `api.services.derivados`, `api.services.fair_value`); lo importan las vistas y componentes que consumen esos endpoints.

## Lo usan (backlinks) ←
- [[web.cmp.curvas-chart]]  ·  _component_
- [[web.cmp.fair-value-modal]]  ·  _component_
- [[web.cmp.fair-value-view]]  ·  _component_
- [[web.cmp.forward-matrix-zscore]]  ·  _component_
- [[web.cmp.forwards-panel]]  ·  _component_
- [[web.cmp.renta-fija-live]]  ·  _component_
- [[web.cmp.renta-fija-table]]  ·  _component_
- [[web.cmp.watchlist-panel]]  ·  _component_
- [[web.view.renta-fija.view]]  ·  _view_
