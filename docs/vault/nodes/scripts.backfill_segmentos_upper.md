---
id: scripts.backfill_segmentos_upper
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_segmentos_upper.py
---

# scripts/backfill_segmentos_upper

> backfill_segmentos_upper.py — normaliza nivel_1..5 a MAYÚSCULAS en Comitentes.

**Archivo:** `scripts/backfill_segmentos_upper.py`

## Qué hace
Migración one-shot que normaliza a MAYÚSCULAS los campos de segmentación nivel_1..5 en Clientes.Comitentes, para evitar categorías duplicadas por capitalización ("Productores" vs "PRODUCTORES"). Aplica `$toUpper` server-side por campo; es idempotente (no-op sobre valores ya en mayúsculas). Se corre una sola vez porque los endpoints de edición de clientes ya hacen `.upper()` al persistir. Se invoca `python -m scripts.backfill_segmentos_upper` (dry-run) y `--apply` para ejecutar.
Conecta con: Clientes.Comitentes (escribe), convención compartida con api/routers/manager/clientes.py, core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Productores]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
