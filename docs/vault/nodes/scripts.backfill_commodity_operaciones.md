---
id: scripts.backfill_commodity_operaciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_commodity_operaciones.py
---

# scripts/backfill_commodity_operaciones

> Backfill del campo `commodity` (SOJA/TRIGO/MAIZ/None) en CashFlow.Operaciones.

**Archivo:** `scripts/backfill_commodity_operaciones.py`

## Qué hace
Backfill que materializa el campo `commodity` (SOJA/TRIGO/MAIZ/None) en los docs ya existentes de `CashFlow.Operaciones` y crea el índice `commodity_concertacion`, para que `/ops/agro` matchee por índice parcial en vez de escanear con regex. Lo hace server-side (un `update_many` + pipeline, sin cursor — la versión con cursor moría con CursorNotFound sobre 481k docs). Replica exactamente `operaciones_informes.clasificar_commodity`.
Se corre con `python -m scripts.backfill_commodity_operaciones [--dry-run]`.
Conecta con: `api.services.operaciones_informes.ensure_indexes`; escribe `CashFlow.Operaciones`.

## Usa / conecta con →
- [[api.services.operaciones_informes]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
