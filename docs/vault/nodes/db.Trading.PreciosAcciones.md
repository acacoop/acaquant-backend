---
id: db.Trading.PreciosAcciones
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.PreciosAcciones

> Colección Mongo en DB Trading.

## Qué hace
Serie time-series de precios diarios de acciones/activos en la base `Trading` (1 vela daily por activo). Alimenta el Scanner de Renta Variable y los cálculos técnicos (pivot points, rolling stats).

Conecta con: la escribe `jobs/precios_acciones_daily.py`; la leen `api/services/scanner.py`, `rv_motor.py`, `quant/pivot_points.py` y el MCP server (scanner). Validación de consistencia en `api/routers/manager/checks.py`.

## Lo usan (backlinks) ←
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[jobs.precios_acciones_daily]]  ·  _module_
- [[quant.pivot_points]]  ·  _module_
- [[scripts.backfill_precios_acciones]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
