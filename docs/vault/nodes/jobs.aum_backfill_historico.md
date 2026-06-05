---
id: jobs.aum_backfill_historico
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/aum_backfill_historico.py
---

# jobs/aum_backfill_historico

> aum_backfill_historico.py — backfill de cierres de mes (jul-2025 → feb-2026).

**Archivo:** `jobs/aum_backfill_historico.py`

## Qué hace
Backfill de cierres de fin de mes (jul-2025 → feb-2026, antes de que existiera el daily). A diferencia de `aum_backfill`, usa el universo completo de id_cuenta vistos alguna vez en AuM (cubre cuentas ya cerradas), fija `fecha_snapshot` al último día calendario del mes y manda ese día como `desde` a Aunesa. Cada (run, fecha, cuenta) deja un doc auditable de outcome (ok/timeout/error/sin_datos), con modo `--solo-fallidas` para reintentar dirigido.

Conecta con: pega a Aunesa, escribe `Valuaciones.AuM`, registra outcomes en `Manager.AumBackfillLog`, sincroniza `Valuaciones.Assets`. Reusa helpers de `jobs.aum`. Herramienta manual.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[jobs.aum]]  ·  _module_
