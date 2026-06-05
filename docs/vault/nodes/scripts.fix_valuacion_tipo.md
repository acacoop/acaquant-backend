---
id: scripts.fix_valuacion_tipo
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/fix_valuacion_tipo.py
---

# scripts/fix_valuacion_tipo

> fix_valuacion_tipo.py — recalcula la valuacion de Valuaciones.AuM para

**Archivo:** `scripts/fix_valuacion_tipo.py`

## Qué hace
One-shot de corrección que recalcula la `valuacion` de Valuaciones.AuM para un tipoTitulo dado en todos los snapshots donde aparece. Se usa cuando un tipo estaba mal clasificado en jobs/aum.py (no dividía /100) y se corrigió: los docs viejos quedaron inflados x100. No toca el precio, solo recalcula con la lógica actual del job. Default: 'Letras de Liquidez del Banco Central' (LELIQ/LEFI). Dry-run por default. Se corre con `python -m scripts.fix_valuacion_tipo [--tipo "..."] [--apply]`.
Conecta con: usa jobs.aum._calcular_valuacion; escribe Valuaciones.AuM.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[jobs.aum]]  ·  _module_
