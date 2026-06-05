---
id: cron.jobs.actividad_mensual
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.actividad_mensual

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 22:30 UTC (19:30 ART) que corre `jobs.actividad_mensual`: arma el snapshot mensual de cuentas activas (cuántas operaron en el mes) para el seguimiento comercial.

Conecta con: ejecuta `jobs/actividad_mensual.py`; lee actividad de `CashFlow.NegocioMovimientos` y persiste el agregado mensual a Mongo. Timeout 15m.

## Usa / conecta con →
- [[jobs.actividad_mensual]]  ·  _module_
