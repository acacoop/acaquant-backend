---
id: web.cmp.cashflow-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/cashflow-view.tsx
---

# web/components/cashflow-view

**Archivo:** `src/components/cashflow-view.tsx`

## Qué hace
Vista de Cash Flow: grafica los flujos brutos (entradas/salidas) por moneda (ARS/USD) con granularidad diaria o mensual, y permite filtrar por relación con accionistas (todas / sin accionistas / solo accionistas / solo cooperativas). Usa color distinto para ARS vs USD y para flujos positivos vs negativos.

Conecta con: consume los endpoints de cashflow (`/api/...`, alimentados por `jobs/cashflow.py`/`flujo_contrapartes.py`); cruza con la lista de accionistas. Usa `date-picker`.

_Sin conexiones detectadas mecánicamente._
