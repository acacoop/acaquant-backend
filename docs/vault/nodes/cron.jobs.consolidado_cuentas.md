---
id: cron.jobs.consolidado_cuentas
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.consolidado_cuentas

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 23:30 UTC (20:30 ART, después del AuM final de 23:00) que corre `jobs.consolidado_cuentas`: precalcula la valuación consolidada por cuenta para que la API no la arme en caliente.

Conecta con: ejecuta `jobs/consolidado_cuentas.py`; lee `Valuaciones.AuM` y escribe a `Valuaciones.ConsolidadoCuentas`, leído por la API de valuaciones. Timeout 25m.

## Usa / conecta con →
- [[jobs.consolidado_cuentas]]  ·  _module_
