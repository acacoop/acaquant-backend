---
id: web.cmp.tradingview-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/tradingview-chart.tsx
---

# web/components/tradingview-chart

**Archivo:** `src/components/tradingview-chart.tsx`

## Qué hace
Wrapper del widget Advanced Chart de TradingView (gratis, sin API key). Traduce los tickers internos de las watchlists al formato de TradingView (forex FX:, treasuries TVC:, MERVAL BCBA:IMV, futuros CME/CBOT/COMEX con front-month, etc.) y monta el gráfico embebido para el símbolo elegido.

Conecta con: carga el script externo de TradingView; no pega a la API propia. Lo usan ticker-chart-panel (Scanner) y watchlist-panel.

## Lo usan (backlinks) ←
- [[web.cmp.home-view]]  ·  _component_
