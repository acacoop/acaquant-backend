---
id: cron.jobs.cashflow
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.cashflow

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron martes a sábado a 02:00 UTC (L-V 23:00 ART) que corre `jobs.cashflow --today` y encadena `sync_api_copies --movimientos`: procesa los movimientos de cashflow del día y luego re-sincroniza las colecciones API derivadas.

Conecta con: ejecuta `jobs/cashflow.py` + `jobs/sync_api_copies.py`; escribe a las colecciones de cashflow/movimientos y propaga a las copias `*API.*API`. Timeout 30m.

## Usa / conecta con →
- [[jobs.cashflow]]  ·  _module_
