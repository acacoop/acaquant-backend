---
id: cron.jobs.sync_comitentes
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.sync_comitentes

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V en 3 corridas (14/17/21 UTC) que corre `jobs.sync_comitentes`: sincroniza las cuentas comitentes desde Aunesa hacia el master `Clientes.Comitentes` (altas/bajas/cambios de datos).

Conecta con: ejecuta `jobs/sync_comitentes.py`; usa `core/aunesa.py` y escribe a `Clientes.Comitentes`, base del Tablero Comercial y la segmentación. Timeout 15m.

## Usa / conecta con →
- [[jobs.sync_comitentes]]  ·  _module_
