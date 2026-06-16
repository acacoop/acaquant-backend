---
id: api.routers.market
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\market.py
---

# api/routers/market

> Router Market: watchlist quotes, economic calendar, candles históricos.

**Archivo:** `api\routers\market.py`

## Qué hace
Router de datos de mercado global para watchlists y gráficos. Expone `/api/market/quotes` (últimas cotizaciones de equities/forex con retornos 7d/MTD/YTD/1Y calculados on-the-fly desde anchors), `/calendar/economic` (calendario económico filtrable por país/importancia), `/candle` (OHLC histórico vía Yahoo Finance) y `/profile` (perfil de empresa vía Finnhub).

Conecta con: lee `Market.Quotes` (poblada por `jobs.market_quotes`) y `Market.EconomicCalendar`; pega a Yahoo (`core.yahoo`) y Finnhub (`core.finnhub`) en vivo; lo consume el frontend de la home/watchlist.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.market_sql]]  ·  _module_
- [[core.finnhub]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.yahoo]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.api.api.market.calendar]]  ·  _route_
- [[web.api.api.market.quotes]]  ·  _route_
- [[web.cmp.watchlist-panel]]  ·  _component_
