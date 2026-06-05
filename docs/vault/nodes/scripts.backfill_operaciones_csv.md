---
id: scripts.backfill_operaciones_csv
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_operaciones_csv.py
---

# scripts/backfill_operaciones_csv

> backfill_operaciones_csv.py — carga un CSV a CashFlow.Operaciones, directo

**Archivo:** `scripts/backfill_operaciones_csv.py`

## Qué hace
Herramienta reusable para cargar un CSV de operaciones a CashFlow.Operaciones sin pasar por el navegador, usando el mismo normalizador que la vista de Manager. Mucho más rápida que el upload web: lee local, deduplica por boleto y upsertea en lotes contra Atlas (modo `--fresh` dropea + insert_many + índice al final para la carga histórica inicial). Idempotente: re-correr actualiza, no duplica. Se corre `python -m scripts.backfill_operaciones_csv --csv operaciones.csv`; después se enriquece con `scripts.enrich_operaciones`.
Conecta con: CashFlow.Operaciones (escribe), api.services.operaciones_informes (normalizar/ingestar/índices), core.mongo.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
