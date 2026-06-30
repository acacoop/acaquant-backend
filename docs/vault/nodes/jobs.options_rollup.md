---
id: jobs.options_rollup
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/options_rollup.py
---

# jobs/options_rollup

> options_rollup.py — rollup diario de mercado.options_data (SQL) → mercado.options_data_hist (SQL).

**Archivo:** `jobs/options_rollup.py`

## Qué hace
Job de rollup diario de opciones: agrupa todos los ticks intradía de `Opciones.Data` en una fila por (fecha, symbol) con OHLC del día (high/low/last), EV máximo y los griegos/IV/spot del último tick. Idempotente por upsert. Modos: día actual (cron 20:15 UTC L-V), `--fecha` puntual o `--backfill` de todo el histórico disponible.

Conecta con: lee `Opciones.Data` (ticks del motor de opciones GGAL), escribe `Opciones.DataHistorica`. Esta serie histórica la consume el módulo de opciones (`api.services.opciones`).

## Usa / conecta con →
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.options_rollup]]  ·  _cron_
