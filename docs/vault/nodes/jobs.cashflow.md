---
id: jobs.cashflow
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/cashflow.py
---

# jobs/cashflow

**Archivo:** `jobs/cashflow.py`

## Qué hace
Captura los movimientos de efectivo de las cuentas comitente: pega a Aunesa `consolidadosGenerales` día hábil por día, filtra las filas que son depósitos/transferencias/extracciones (match por palabra clave) e invierte el signo (entradas positivas, salidas negativas). Persiste idempotente por `comprobante` (índice único, `$setOnInsert`); descarta defensivamente las filas sin comprobante para no tumbar el bulk_write. `--today` para el cron diario (corre 02:00 UTC ≈ 23:00 ART).

Conecta con: pega a Aunesa, escribe `CashFlow.Movimientos`. Cron diario en `deploy/crontab.txt`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.cashflow]]  ·  _cron_
