---
id: web.cmp.ops-bar-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/ops-bar-chart.tsx
---

# web/components/ops-bar-chart

**Archivo:** `src/components/ops-bar-chart.tsx`

## Qué hace
Gráfico de barras único de la sección /operaciones (Operaciones · Aranceles · Agro). Recibe siempre una serie diaria y agrega/filtra en el cliente: toolbar con DIARIO/SEMANAL/MENSUAL, rango (1W…ALL), navegación ◀▶ por período, foco día y maximizar — los toggles no re-fetchean. Soporta una sola serie o multi-serie (Agro: SOJA/TRIGO/MAIZ).

Conecta con: lo consumen ops-view (y las vistas de aranceles/agro). No pega a la API directamente — recibe la serie ya cargada por la vista padre y la rerenderiza con recharts.

_Sin conexiones detectadas mecánicamente._
