---
id: jobs.actividad_mensual
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\actividad_mensual.py
---

# jobs/actividad_mensual

> actividad_mensual.py — snapshot mensual de CUENTAS ACTIVAS.

**Archivo:** `jobs\actividad_mensual.py`

## Qué hace
Snapshot mensual de "cuentas activas": una cuenta cuenta como activa en el mes M si tuvo al menos un boleto operativo con fecha dentro de ese mes. Agrupa `CashFlow.NegocioMovimientos` por (mes, id_cuenta) y congela en cada doc el operador/segmento vigente al momento de correr (historia point-in-time). Corre el mes corriente, un mes puntual o backfill de todos los meses; es idempotente (borra y reescribe los meses tocados).

Conecta con: lee `CashFlow.NegocioMovimientos` + `Clientes.Comitentes`, escribe `Clientes.ActividadMensual`; reusa `_CATS_OPERACIONES`/`_PESIF` de `api.services.comercial` y registra el run en `Manager.JobRuns`.

## Usa / conecta con →
- [[api.services.comercial]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.actividad_mensual]]  ·  _cron_
