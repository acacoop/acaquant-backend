---
id: web.cmp.payoff-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/payoff-chart.tsx
---

# web/components/payoff-chart

**Archivo:** `src/components/payoff-chart.tsx`

## Qué hace
Gráfico de payoff (P&L al vencimiento) de una estrategia de opciones. Toma las patas resueltas, el spot y el costo, calcula la curva de resultado y los breakevens con los helpers de lib/estrategias, y la dibuja como área verde (ganancia) / roja (pérdida) con líneas de referencia. Parte de la Mesa de Estrategia de Renta Variable.

Conecta con: cálculo puro en el cliente vía lib/estrategias (payoffCurve, findBreakevens). Sin llamadas a la API; recibe las patas ya armadas por la vista de estrategia.

_Sin conexiones detectadas mecánicamente._
