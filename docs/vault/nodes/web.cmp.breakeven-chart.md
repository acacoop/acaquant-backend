---
id: web.cmp.breakeven-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/breakeven-chart.tsx
---

# web/components/breakeven-chart

**Archivo:** `src/components/breakeven-chart.tsx`

## Qué hace
Gráfico de líneas (recharts) que dibuja el breakeven mensual de inflación implícito en cada par Lecap↔CER, por días al vencimiento. Marca una línea de referencia horizontal (3%) como umbral de comparación. Es un componente de presentación puro.

Conecta con: recibe el array `pares` (breakevens calculados por `engines/breakevens.py` / service de derivados) desde su contenedor; no hace fetch.

_Sin conexiones detectadas mecánicamente._
