---
id: jobs.ops_rollup
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/ops_rollup.py
---

# jobs/ops_rollup

> jobs/ops_rollup.py — precompute CashFlow.OpsSerieDiaria (rollup de las series).

**Archivo:** `jobs/ops_rollup.py`

## Qué hace
Job de precompute que pre-agrega `CashFlow.Operaciones` por día × dimensiones de baja cardinalidad (moneda, mercado, operación, segmento, nivel_3) sumando bruto, arancel (en valor absoluto) y conteo. Resuelve el problema de que las series /ops/serie y /ops/aranceles escaneaban los ~200k docs completos en cada request. Modo incremental (últimos 7 días) o `--full` con swap atómico.

Conecta con: lee `CashFlow.Operaciones`, escribe `CashFlow.OpsSerieDiaria` (vía `reemplazar_coleccion_atomico` en full). Lo leen los endpoints `/ops/serie` y `/ops/aranceles` (el día de hoy se agrega live-fallback). Registra el run en `Manager.JobRuns`.

## Usa / conecta con →
- [[core.job_runs]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.ops_rollup]]  ·  _cron_
