---
id: web.cmp.agro-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/agro-view.tsx
---

# web/components/agro-view

**Archivo:** `src/components/agro-view.tsx`

## Qué hace
Vista OPERACIONES → AGRO: toneladas operadas de futuros agropecuarios. Fila superior con 3 tablas (por commodity, por cuenta, por instrumento) y fila inferior con 2 gráficos (volumen global y de la cuenta elegida), más un modo "share" que compara volumen nuestro vs mercado por commodity. Toolbar de rango desde→hasta con cross-filter por click; las tablas se acotan al rango y los charts agregan la serie diaria en cliente.

Conecta con: pollea `GET /api/operaciones/ops/agro`; usa `date-picker` y `ops-bar-chart`.

_Sin conexiones detectadas mecánicamente._
