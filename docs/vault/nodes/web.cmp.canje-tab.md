---
id: web.cmp.canje-tab
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/canje-tab.tsx
---

# web/components/canje-tab

**Archivo:** `src/components/canje-tab.tsx`

## Qué hace
Pestaña que grafica la serie histórica del canje CCL/MEP intra-bono (ej. AL30C/AL30D − 1) para los pares AL30 y GD30. Línea temporal con los precios de cada pata y el canje resultante; pollea cada 5 minutos.

Conecta con: pollea `GET /api/analitica/canje` (service `canje.py`), que lee `Trading.CanjeCierre`. Usa `use-viewport-key` para re-render responsive.

_Sin conexiones detectadas mecánicamente._
