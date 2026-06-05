---
id: scripts.cleanup_negocio_informacion
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/cleanup_negocio_informacion.py
---

# scripts/cleanup_negocio_informacion

> cleanup_negocio_informacion.py — borra docs ruido de CashFlow.NegocioMovimientos.

**Archivo:** `scripts/cleanup_negocio_informacion.py`

## Qué hace
Limpieza reusable que borra de CashFlow.NegocioMovimientos los docs ruido cuyo campo `informacion` contiene cualquiera de los substrings de la lista canónica de exclusión, la misma que aplica el filtro de ingesta de aunesa_negocio. Muestra un breakdown por `informacion` antes de borrar y permite acotar por rango de fechas. Idempotente, dry-run por default. Se corre `python -m scripts.cleanup_negocio_informacion [--desde YYYY-MM-DD]` y `--apply` para borrar.
Conecta con: CashFlow.NegocioMovimientos (borra), api.services._negocio_informacion_filter (lista de substrings), core.mongo.

## Usa / conecta con →
- [[api.services._negocio_informacion_filter]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
