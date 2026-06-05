---
id: cron.jobs.volatilidad_ggal
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.volatilidad_ggal

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 20:00 UTC (17:00 ART, al cierre) que corre `jobs.volatilidad_ggal`: calcula la volatilidad de GGAL para alimentar el pricing de su cadena de opciones.

Conecta con: ejecuta `jobs/volatilidad_ggal.py`; lee precios de GGAL y persiste la volatilidad usada por el módulo de opciones. Timeout 15m.

## Usa / conecta con →
- [[jobs.volatilidad_ggal]]  ·  _module_
