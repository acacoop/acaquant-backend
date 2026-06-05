---
id: cron.jobs.precios_acciones_daily
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.precios_acciones_daily

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 22:00 UTC (19:00 ART, post cierre US) que corre `jobs.precios_acciones_daily`: agrega una vela diaria por activo a la time-series `Trading.PreciosAcciones`, que alimenta el Scanner de Renta Variable y los pivot points.

Conecta con: ejecuta `jobs/precios_acciones_daily.py`; escribe a `Trading.PreciosAcciones`; leído por `quant/pivot_points.py` y `api/services/scanner.py`. Timeout 30m.

## Usa / conecta con →
- [[jobs.precios_acciones_daily]]  ·  _module_
