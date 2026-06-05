---
id: core.yahoo
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/yahoo.py
---

# core/yahoo

> Cliente Yahoo Finance vía yfinance (gratis, sin API key).

**Archivo:** `core/yahoo.py`

## Qué hace
Cliente de Yahoo Finance vía la librería `yfinance` (gratis, sin API key). Reemplazó a Finnhub para histórico cuando este bloqueó `/stock/candle` con 403. Expone `yahoo_quote()` (último + previous close) y `stock_candle()` (OHLCV histórico), ambos emulando el shape de Finnhub (`{s,t,o,h,l,c,v}`) para que los callers migren sin tocar nada. Cubre acciones US, ETFs, ADRs, índices globales y yields de Treasury (^IRX, ^TNX).

Conecta con: la API pública de Yahoo Finance (yfinance). Lo invocan jobs de precios/watchlist (ej. `jobs.adr_live`, `jobs.market_quotes`) y el router `api.routers.market` para candles históricos.

## Lo usan (backlinks) ←
- [[api.routers.market]]  ·  _module_
- [[jobs.market_anchors]]  ·  _module_
- [[jobs.market_quotes]]  ·  _module_
- [[jobs.precios_acciones_daily]]  ·  _module_
- [[scripts.backfill_precios_acciones]]  ·  _module_
