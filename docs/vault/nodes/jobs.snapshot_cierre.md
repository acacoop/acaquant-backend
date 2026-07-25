---
id: jobs.snapshot_cierre
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/snapshot_cierre.py
---

# jobs/snapshot_cierre

> snapshot_cierre.py — materializa el cierre diario por bono SQL-NATIVE.

**Archivo:** `jobs/snapshot_cierre.py`

## Qué hace
Job que materializa el cierre diario por bono (tasa fija + CER) leyendo directo de `Trading.MarketSnapshot` post-cierre (corre 17:25 ART, cuando el motor ya no escribe). Toma last_price, total_nominals y los analíticos (TEA/TEM/duration/paridad) que dejaron los motores durante la rueda, y los persiste como cierre del día. Guard contra feriados: si last_price o total_nominals es 0, skipea para no congelar valores stale. Idempotente (upsert por ts_cierre/curva/ticker).

Conecta con: lee `Trading.Curvas` (metadata) + `Trading.MarketSnapshot` (estado live), escribe `Trading.SnapshotsCierre`. Encadena con `jobs.fair_value` (cierre_chain). Esa serie alimenta análisis de renta fija históricos.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.snapshot_cierre]]  ·  _cron_
