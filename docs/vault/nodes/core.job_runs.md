---
id: core.job_runs
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\job_runs.py
---

# core/job_runs

> Context manager para registrar runs de jobs automáticos en manager.job_runs (SQL).

**Archivo:** `core\job_runs.py`

## Qué hace
Context manager `JobRunLogger("tipo")` para instrumentar los cron jobs: captura stdout, contadores estructurados (`set_stat`), errores non-fatal, duración y estado (ok/partial/error), y al salir persiste un doc resumen en `Manager.JobRuns` sin perder los logs de archivo. Guarda las últimas ~200 líneas de log.

Conecta con: escribe `Manager.JobRuns` (TTL creado en `scripts/crear_indices.py`); lo envuelven los jobs batch (aum, carteras, negocio_movimientos, etc.); lo lee el panel de `/manager` (status e historial de jobs).

## Usa / conecta con →
- [[core.pg_mirror]]  ·  _module_

## Lo usan (backlinks) ←
- [[jobs.acreencias]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.adr_live]]  ·  _module_
- [[jobs.aranceles]]  ·  _module_
- [[jobs.argentina_datos]]  ·  _module_
- [[jobs.backfill_tasas]]  ·  _module_
- [[jobs.bcra]]  ·  _module_
- [[jobs.bcra_research]]  ·  _module_
- [[jobs.bonos_ohlc_daily]]  ·  _module_
- [[jobs.cashflow]]  ·  _module_
- [[jobs.cedears_bars_1m]]  ·  _module_
- [[jobs.cedears_ohlc_daily]]  ·  _module_
- [[jobs.cleanup_cedears_timesales]]  ·  _module_
- [[jobs.cleanup_retencion]]  ·  _module_
- [[jobs.consolidado_cuentas]]  ·  _module_
- [[jobs.controles_datos]]  ·  _module_
- [[jobs.day_trading_stats]]  ·  _module_
- [[jobs.eikon_cierres]]  ·  _module_
- [[jobs.estrategia_resolver]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.fred_research]]  ·  _module_
- [[jobs.guardrails]]  ·  _module_
- [[jobs.ia_calidad]]  ·  _module_
- [[jobs.mercado_1816_series]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.pnl_totales_precompute]]  ·  _module_
- [[jobs.portafolio_backfill]]  ·  _module_
- [[jobs.research_mail]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[jobs.snapshot_sinteticos]]  ·  _module_
- [[jobs.sync_comitentes]]  ·  _module_
- [[jobs.triage]]  ·  _module_
