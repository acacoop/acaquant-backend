---
id: scripts.diag_categoria_otro
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_categoria_otro.py
---

# scripts/diag_categoria_otro

> diag_categoria_otro.py — distinct `informacion` con categoria='otro' o null.

**Archivo:** `scripts/diag_categoria_otro.py`

## Qué hace
Diagnóstico read-only que lista los valores distintos de `informacion` que la función categorizar() de aunesa_negocio deja sin clasificar (categoria='otro' o null), ordenados por frecuencia descendente. Cada fila es candidata a sumarse como regla nueva para que deje de caer al fallback; el orden prioriza por impacto (las que más se repiten primero). Se corre con python -m scripts.diag_categoria_otro [--top 50] [--desde YYYY-MM-DD].
Conecta con: CashFlow.NegocioMovimientos (lectura), core.mongo. Apunta a mejorar api.services.aunesa_negocio.categorizar().

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
