---
id: jobs.backfill_breakevens
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\backfill_breakevens.py
---

# jobs/backfill_breakevens

> Backfill histórico de Trading.BreakevensHistorico.

**Archivo:** `jobs\backfill_breakevens.py`

## Qué hace
Reconstruye día a día la tabla histórica de breakevens CER/Lecap del rango pedido, usando la MISMA lógica del motor live (`engines.breakevens.cargar_pares` + `calcular_breakevens`) para garantizar paridad. Por cada día arma el último TEM/paridad/TEA/precio por bono desde TimeSales y el CER vigente (con el forward de ~10 hábiles), y reconstruye la foto tal cual era ese día (sin filtrar por IPC publicado). Idempotente por fecha; saltea hoy por default.

Conecta con: lee `Trading.TimeSales`, `Trading.CER`, `Trading.DiasHabiles` + `Trading.Curvas` (vía engine); escribe `Trading.BreakevensHistorico`. Herramienta manual.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[engines.breakevens]]  ·  _module_
