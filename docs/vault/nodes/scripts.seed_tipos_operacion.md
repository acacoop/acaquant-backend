---
id: scripts.seed_tipos_operacion
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/seed_tipos_operacion.py
---

# scripts/seed_tipos_operacion

> seed_tipos_operacion.py — catálogo CashFlow.TiposOperacion (tipo → mercado).

**Archivo:** `scripts/seed_tipos_operacion.py`

## Qué hace
Seed idempotente del catálogo CashFlow.TiposOperacion: la tabla MERCADOS que mapea cada `tipo_operacion` a un `mercado` (BYMA, SENEBI, MAV, Primario, FCI, etc.) para poder filtrar operaciones por mercado; el `operacion` (compra/venta/caución) se deriva solo. Junta los DISTINCT reales de CashFlow.Operaciones con una lista base conocida. Upsert por tipo_operacion: refresca el derivado pero nunca pisa el `mercado` que cargaste a mano ($setOnInsert). Uso: `python -m scripts.seed_tipos_operacion [--dry]`.

Conecta con: CashFlow.TiposOperacion (escribe), CashFlow.Operaciones (lee tipos), core.mongo (rw).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
