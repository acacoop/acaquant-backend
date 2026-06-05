---
id: cron.jobs.market_anchors
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.market_anchors

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 22:00 UTC (post-cierre US) que corre `jobs.market_anchors`: calcula los anchors de retorno (7d, MTD, YTD, 1Y) del watchlist, los puntos de referencia contra los que se mide la performance.

Conecta con: ejecuta `jobs/market_anchors.py`; lee la historia de precios y persiste los anchors a Mongo, leídos por el módulo Market. Timeout 15m.

## Usa / conecta con →
- [[jobs.market_anchors]]  ·  _module_
