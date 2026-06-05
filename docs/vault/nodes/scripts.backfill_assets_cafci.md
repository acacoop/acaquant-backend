---
id: scripts.backfill_assets_cafci
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_assets_cafci.py
---

# scripts/backfill_assets_cafci

> backfill_assets_cafci.py — auto-fill del campo CAFCI en

**Archivo:** `scripts/backfill_assets_cafci.py`

## Qué hace
Backfill idempotente que auto-rellena el campo `CAFCI` (derivado de `unidad` por regex) en los docs ya existentes de `Valuaciones.Assets`. El cron de AuM (`jobs/aum.py::_sincronizar_assets`) ya lo mantiene en sync para upserts nuevos; este script cubre los docs persistidos antes del fix. Solo escribe cuando el valor calculado difiere del actual.
Se corre con `python -m scripts.backfill_assets_cafci [--dry]`.
Conecta con: `core.cafci.extract_cafci`; escribe `Valuaciones.Assets`.

## Usa / conecta con →
- [[core.cafci]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
