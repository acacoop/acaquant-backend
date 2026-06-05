---
id: cron.jobs.negocio_movimientos
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.negocio_movimientos

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron cada hora 15-22 UTC (L-V) que corre la cadena de negocio del día: `jobs.negocio_movimientos` (pega a Aunesa consolidadosGenerales → `CashFlow.NegocioMovimientos`, idempotente) + `jobs.aranceles` (rellena el campo `arancel`) + `jobs.fci_bilateral` (lleva el FCI bilateral a Operaciones).

Conecta con: ejecuta `jobs/negocio_movimientos.py`, `jobs/aranceles.py`, `jobs/fci_bilateral.py`; usa `core/aunesa.py`; escribe a `CashFlow.NegocioMovimientos`, base de la vista `/operaciones/negocio`. Timeout 25m.

## Usa / conecta con →
- [[jobs.negocio_movimientos]]  ·  _module_
