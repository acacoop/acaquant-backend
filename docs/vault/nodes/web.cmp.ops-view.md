---
id: web.cmp.ops-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/ops-view.tsx
---

# web/components/ops-view

**Archivo:** `src/components/ops-view.tsx`

## Qué hace
Vista Operaciones → Movimientos sobre CashFlow.Operaciones. Layout 50/50: a la izquierda Σbruto por operación y el gráfico de barras (OpsBarChart) por fecha; a la derecha Σbruto por denominación. Cross-filter: elegir una operación o denominación filtra la otra tabla y el gráfico. Filtros por moneda (ARS/USD), segmento y búsqueda; excluye los "Cierre" del lado servidor.

Conecta con: pega a los endpoints /api/ops/* (boletos, series por operación/denominación, meta) servidos por api.routers.operaciones sobre CashFlow.Operaciones. Embebe ops-bar-chart.

_Sin conexiones detectadas mecánicamente._
