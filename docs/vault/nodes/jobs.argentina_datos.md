---
id: jobs.argentina_datos
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/argentina_datos.py
---

# jobs/argentina_datos

> Cron: pega argentinadatos.com y persiste riesgo país / IPC / REM en SQL (SQL-only).

**Archivo:** `jobs/argentina_datos.py`

## Qué hace
Cron diario que baja indicadores macro argentinos públicos de argentinadatos.com y los persiste: riesgo país, inflación mensual e interanual (shape `{fecha, valor}` con sanity-check por rango) y el REM del BCRA filtrado a un solo indicador (IPC nivel general INDEC) con estadísticos de consenso. El REM normaliza el período a YYYY-MM y es idempotente por (informe, periodo, periodo_tipo).

Conecta con: usa `core.argentina_datos`, escribe `Trading.RiesgoPais`, `Trading.InflacionMensual`, `Trading.InflacionInteranual` y `Trading.REM`; registra el run en `Manager.JobRuns`. Lo consumen los services macro/argy/REM de la API.

## Usa / conecta con →
- [[core.argentina_datos]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.argentina_datos]]  ·  _cron_
