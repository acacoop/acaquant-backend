---
id: web.cmp.aranceles-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/aranceles-view.tsx
---

# web/components/aranceles-view

**Archivo:** `src/components/aranceles-view.tsx`

## Qué hace
Vista OPERACIONES → ARANCELES: suma de aranceles cobrados, con tabla por `nivel_3` y gráfico a la izquierda, y tabla por cliente a la derecha. Filtros por moneda (ARS/USD), segmento (`nivel_1`) y fechas; modos ÚLTIMA/DÍA/TODOS. El gráfico trae la serie diaria (acotada a ~18m por performance, o full si se pide "ALL") y agrega en cliente.

Conecta con: pollea `GET /api/operaciones/ops/aranceles`; usa `ops-bar-chart`. Los aranceles se completan en `CashFlow.NegocioMovimientos` vía los jobs de aranceles.

_Sin conexiones detectadas mecánicamente._
