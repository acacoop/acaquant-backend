---
id: cron.jobs.snapshot_cierre
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.snapshot_cierre

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 20:25 UTC (17:25 ART) que corre la cadena de cierre: `jobs.snapshot_cierre` (materializa el cierre diario por bono leyendo `MarketSnapshot` → `Trading.SnapshotsCierre`) + `jobs.fair_value` (fit cuadrático + residuos + z-scores).

Conecta con: ejecuta `jobs/snapshot_cierre.py` + `jobs/fair_value.py`; lee `Trading.MarketSnapshot`, escribe `Trading.SnapshotsCierre`; el fair_value usa `api/services/fair_value.py`. Timeout 25m.

## Usa / conecta con →
- [[jobs.snapshot_cierre]]  ·  _module_
