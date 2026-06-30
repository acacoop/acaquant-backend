---
id: web.cmp.home-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/home-view.tsx
---

# web/components/home-view

**Archivo:** `src/components/home-view.tsx`

## Qué hace
Vista de inicio (/). Layout con watchlist, panel de noticias y un chart central que conmuta según el ticker seleccionado: TradingView para acciones/índices, o la curva DLR entera cuando el ticker es un futuro de dólar; ARGY/MEP/CCL/Oficial caen al default MERVAL. Soporta maximizar el chart con Escape.

Conecta con: compone `WatchlistPanel`, `NewsPanel`, `TradingViewChart` y `FuturosDlrCurveChart`. Indirectamente consume los endpoints de cada uno (watchlist, news, futuros DLR).

## Usa / conecta con →
- [[web.cmp.canje-tab]]  ·  _component_
- [[web.cmp.futuros-dlr-curve-chart]]  ·  _component_
- [[web.cmp.news-panel]]  ·  _component_
- [[web.cmp.retorno-total-mini]]  ·  _component_
- [[web.cmp.tradingview-chart]]  ·  _component_
- [[web.cmp.watchlist-panel]]  ·  _component_

## Lo usan (backlinks) ←
- [[web.view.(home).view]]  ·  _view_
