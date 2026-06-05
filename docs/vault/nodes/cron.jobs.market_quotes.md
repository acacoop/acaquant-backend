---
id: cron.jobs.market_quotes
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.market_quotes

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron cada 1 minuto en horario US (13-21 UTC, L-V) que corre `jobs.market_quotes`: cotizaciones de equity + forex para los watchlists de la HOME. Timeout 50s (< intervalo) porque yfinance no trae timeout propio y un Yahoo lento apilaba procesos.

Conecta con: ejecuta `jobs/market_quotes.py`; usa `core/yahoo.py` (yfinance) y persiste las quotes a Mongo, leídas por el router `/api/market`.

## Usa / conecta con →
- [[jobs.market_quotes]]  ·  _module_
