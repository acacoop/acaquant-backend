---
id: jobs.precios_acciones_daily
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/precios_acciones_daily.py
---

# jobs/precios_acciones_daily

> precios_acciones_daily.py — agrega 1 vela daily por activo a

**Archivo:** `jobs/precios_acciones_daily.py`

## Qué hace
Job que agrega 1 vela daily (OHLCV) por activo para alimentar el Scanner de Renta Variable. Para cada CEDEAR activo pide a Yahoo los últimos 5 días e inserta solo las fechas que aún no están en la colección (idempotente; la time series Mongo no soporta upsert por (ticker, fecha)). El colchón de 5 días recupera gaps de días/festivos sin lógica extra. Corre 1×/día post-cierre US (22 UTC L-V).

Conecta con: lee underlyings de `Trading.Cedears`, baja precios de Yahoo vía `core.yahoo.stock_candle`, escribe en la time series `Trading.PreciosAcciones`. Esa serie alimenta el Scanner CEDEARs y `quant.pivot_points`.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[core.yahoo]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.precios_acciones_daily]]  ·  _cron_
