---
id: jobs.aum_backfill
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\aum_backfill.py
---

# jobs/aum_backfill

> aum_backfill.py — Corre el snapshot de AuM para una fecha pasada.

**Archivo:** `jobs\aum_backfill.py`

## Qué hace
Corre el snapshot de AuM para una fecha pasada puntual (no el día corriente). Reusa los helpers de `jobs.aum` (auth, consulta, procesado, valuación) pero pidiendo a Aunesa el `desde` correcto para reconstruir el cierre de esa fecha. Soporta acotar a cuentas explícitas o solo a las ya presentes en AuM, con workers/timeout/retries configurables; pisa por (id_cuenta, unidad, fecha_snapshot) y deja las cuentas fallidas en `docs/cuentas_con_error.json` con comando de reintento sugerido.

Conecta con: pega a Aunesa, escribe `Valuaciones.AuM`, sincroniza `Valuaciones.Assets` y dispara `aum_resumen_fci`. Herramienta manual, no va por cron.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[jobs.aum]]  ·  _module_
- [[jobs.aum_resumen_fci]]  ·  _module_
