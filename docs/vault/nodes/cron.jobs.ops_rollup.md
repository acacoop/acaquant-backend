---
id: cron.jobs.ops_rollup
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.ops_rollup

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron cada hora (:40, 13-22 UTC L-V) que corre `jobs.ops_rollup`: pre-agrega incrementalmente las series de operaciones a `CashFlow.OpsSerieDiaria`, para que `/ops/serie` y `/ops/aranceles` lean el rollup en vez de escanear las ~487k filas de Operaciones.

Conecta con: ejecuta `jobs/ops_rollup.py`; lee `CashFlow.Operaciones` y escribe a `CashFlow.OpsSerieDiaria`. Backfill inicial: `--full`. Timeout 15m.

## Usa / conecta con →
- [[jobs.ops_rollup]]  ·  _module_
