---
id: jobs.aum
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\aum.py
---

# jobs/aum

**Archivo:** `jobs\aum.py`

## Qué hace
El cron diario de AuM: autentica contra Aunesa, trae el listado de cuentas activas y consulta en paralelo (8 workers) la posición valuada de cada una a T+2. Procesa cada respuesta (agrupa por especie, aplica reglas de exclusión de `_aum_filters`, valúa según tipo de instrumento: renta fija ÷100, futuros +1, resto directo) y persiste idempotentemente. Al cerrar sincroniza unidades nuevas hacia Assets y pre-materializa el resumen FCI.

Conecta con: pega a Aunesa, escribe `Valuaciones.AuM`; sincroniza `Valuaciones.Assets` + `TitulosAPI.AssetsAPI`; llama `jobs.aum_resumen_fci.sync_fecha`. Helpers reusados por `aum_backfill` y `aum_backfill_historico`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.cafci]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[jobs._aum_filters]]  ·  _module_
- [[jobs.aum_resumen_fci]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[cron.jobs.aum]]  ·  _cron_
- [[jobs.aum_backfill]]  ·  _module_
- [[jobs.aum_backfill_historico]]  ·  _module_
