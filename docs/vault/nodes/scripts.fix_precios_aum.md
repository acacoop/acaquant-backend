---
id: scripts.fix_precios_aum
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/fix_precios_aum.py
---

# scripts/fix_precios_aum

> fix_precios_aum.py — corrige precios mal valuados en Valuaciones.AuM.

**Archivo:** `scripts/fix_precios_aum.py`

## Qué hace
One-shot de corrección que reemplaza precios mal valuados en Valuaciones.AuM (el snapshot 2025-08-31 vino con precios errados de Aunesa) usando un map {unidad: precio} de docs/precios_3108.json, y recalcula la valuación de cada posición con la misma lógica del job (jobs.aum._calcular_valuacion). El precio es global, se aplica a todas las cuentas del snapshot; lo que no está en el JSON no se toca. Dry-run por default. Se corre con `python -m scripts.fix_precios_aum [--snapshot ...] [--apply]`.
Conecta con: lee docs/precios_3108.json y jobs.aum; escribe Valuaciones.AuM.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[jobs.aum]]  ·  _module_
