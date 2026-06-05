---
id: web.cmp.operar-dashboard-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/operar-dashboard-view.tsx
---

# web/components/operar-dashboard-view

**Archivo:** `src/components/operar-dashboard-view.tsx`

## Qué hace
Dashboard de trading manual: buscador de símbolo, order book live (bids/offers + métricas OHLC), envío/cancelación de órdenes LIMIT/MARKET, gestión de órdenes del día (incluye las que entraron por otra plataforma del broker) y panel de cartera por cuenta. Exporta hooks (`usePortfolio`, `useOrdenesDia`) y sub-componentes reutilizados por la vista FCI.

Conecta con: pega a `/api/ordenes` (envío/cancel/listado contra ROFEX, LIVE), order book live y datos de cuenta del broker. Lo monta `operar-shell` (tab DASHBOARD).

_Sin conexiones detectadas mecánicamente._
