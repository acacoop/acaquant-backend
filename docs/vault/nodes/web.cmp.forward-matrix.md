---
id: web.cmp.forward-matrix
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/forward-matrix.tsx
---

# web/components/forward-matrix

**Archivo:** `src/components/forward-matrix.tsx`

## Qué hace
Matriz heatmap de tasas forward entre pares de instrumentos: cada celda es la forward implícita de un par, coloreada en escala min/mediana/max para detectar visualmente las más altas/bajas. Modo LIVE (valor absoluto).

Conecta con: recibe `tickers` y la matriz ya calculada por el backend de forwards (motor `engines.forwards` → service `api.services.derivados`); componente de presentación puro.

_Sin conexiones detectadas mecánicamente._
