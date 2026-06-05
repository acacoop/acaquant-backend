---
id: web.cmp.watchlist-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\watchlist-panel.tsx
---

# web/components/watchlist-panel

**Archivo:** `src\components\watchlist-panel.tsx`

## Qué hace
Panel de watchlist de mercado: cotizaciones (precio, % var) de índices, forex, treasuries, futuros internacionales, más curva DLR y métricas argentinas. Poltea quotes globales cada 30s y los feeds locales (ARGY + futuros DLR) cada 5s por ser live. Selecciona un ticker y lo muestra en el gráfico de TradingView embebido (MERVAL por default).

Conecta con: pega a /api/market/quotes, /api/futuros-dlr y /api/argy (routers api.market, api.derivados, api.argy). Embebe tradingview-chart.

## Usa / conecta con →
- [[api.routers.market]]  ·  _module_
- [[web.lib.types]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.cmp.home-view]]  ·  _component_
