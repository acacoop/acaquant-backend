---
id: cron.jobs.flujo_contrapartes
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.flujo_contrapartes

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 22:00 UTC (19:00 ART) que corre `jobs.flujo_contrapartes` y encadena `sync_api_copies --flujo`: arma el flujo de contrapartes y luego re-sincroniza las copias API derivadas.

Conecta con: ejecuta `jobs/flujo_contrapartes.py` + `jobs/sync_api_copies.py`; lee/escribe `CashFlow.Contrapartes` y propaga a `CuentasAPI.ContrapartesAPI`. Timeout 30m.

## Usa / conecta con →
- [[jobs.flujo_contrapartes]]  ·  _module_
