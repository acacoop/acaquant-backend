---
id: jobs.operaciones_informes
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\operaciones_informes.py
---

# jobs/operaciones_informes

> operaciones_informes.py — ingesta de operaciones desde Aunesa /informes a

**Archivo:** `jobs\operaciones_informes.py`

## Qué hace
Job que es la fuente de verdad de operaciones por CONCERTACIÓN. Cada 30 min (horario de mercado L-V) consulta el endpoint Aunesa /operaciones/informes por cuenta en paralelo (ThreadPool), normaliza cada boleto, suma sus aranceles y upsertea idempotente (índice único por boleto) enriqueciendo inline moneda/mercado/operación/segmento. El universo de cuentas son las que ya operan más las comitentes.

Conecta con: lee de Aunesa vía `core.aunesa` y de `Clientes.Comitentes`; escribe en `CashFlow.Operaciones` usando `api.services.operaciones_informes` (ingestar_filas + cargar_maps_enrich). Registra el run en `Manager.JobRuns` vía `JobRunLogger`. Alimenta las series de `jobs.ops_rollup` y los endpoints `/ops/*`.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.aunesa_informes]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[core]]  ·  _module_
- [[core.aunesa]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.operaciones_informes]]  ·  _cron_
