---
id: scripts.drop_indices_redundantes_operaciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/drop_indices_redundantes_operaciones.py
---

# scripts/drop_indices_redundantes_operaciones

> scripts/drop_indices_redundantes_operaciones.py — dropea índices redundantes.

**Archivo:** `scripts/drop_indices_redundantes_operaciones.py`

## Qué hace
Herramienta de mantenimiento que dropea índices redundantes en CashFlow.Operaciones detectados por audit_db: el índice single `concertacion` ya está cubierto por el compuesto `concertacion_mercado` (es su prefijo), así que borrarlo libera RAM del working set del M10 sin afectar performance. Por defecto solo lista (dry-run); con `--apply` los borra. Se corre con `python -m scripts.drop_indices_redundantes_operaciones [--apply]`.
Conecta con: opera sobre CashFlow.Operaciones; ejecuta hallazgos de la auditoría de índices (audit_db / db_maintenance).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
