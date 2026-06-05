---
id: core.job_runs
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\job_runs.py
---

# core/job_runs

> Context manager para registrar runs de jobs automáticos en Manager.JobRuns.

**Archivo:** `core\job_runs.py`

## Qué hace
Context manager `JobRunLogger("tipo")` para instrumentar los cron jobs: captura stdout, contadores estructurados (`set_stat`), errores non-fatal, duración y estado (ok/partial/error), y al salir persiste un doc resumen en `Manager.JobRuns` sin perder los logs de archivo. Guarda las últimas ~200 líneas de log.

Conecta con: escribe `Manager.JobRuns` (TTL creado en `scripts/crear_indices.py`); lo envuelven los jobs batch (aum, carteras, negocio_movimientos, etc.); lo lee el panel de `/manager` (status e historial de jobs).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.notify]]  ·  _module_

## Lo usan (backlinks) ←
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.aranceles]]  ·  _module_
- [[jobs.argentina_datos]]  ·  _module_
- [[jobs.comercial_rollup]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.ops_rollup]]  ·  _module_
- [[jobs.sync_comitentes]]  ·  _module_
