---
id: scripts.backfill_precios_acciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_precios_acciones.py
---

# scripts/backfill_precios_acciones

> backfill_precios_acciones.py — historia daily de cada activo de

**Archivo:** `scripts/backfill_precios_acciones.py`

## Qué hace
Herramienta reusable que baja la historia diaria (OHLCV) del underlying US de cada CEDEAR activo y la guarda en la time series Trading.PreciosAcciones, en USD (no el CEDEAR local en ARS, que vive aparte). Es la serie histórica del activo real para cálculos quant (pivots, rolling stats). Modo default FILL inserta solo las fechas faltantes (idempotente, rellena huecos viejos); `--reset` borra y rehace por ticker. Se corre `python -m scripts.backfill_precios_acciones [--desde YYYY-MM-DD] [--ticker NVDA] [--reset]`.
Conecta con: Trading.PreciosAcciones (escribe), Trading.Cedears (lee underlyings activos), core.yahoo (fuente yfinance), core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.yahoo]]  ·  _module_
- [[db.Trading.PreciosAcciones]]  ·  _collection_
