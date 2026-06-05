---
id: scripts.enrich_operaciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/enrich_operaciones.py
---

# scripts/enrich_operaciones

> enrich_operaciones.py — denormaliza moneda + mercado + operacion sobre

**Archivo:** `scripts/enrich_operaciones.py`

## Qué hace
Herramienta reusable que denormaliza moneda + mercado + operacion sobre CashFlow.Operaciones haciendo join al catálogo CashFlow.TiposOperacion, y crea los índices de la vista. Hay que correrlo después del backfill y cada vez que se editan los `mercado` del catálogo; es idempotente. Si quedan ops sin match en catálogo, avisa para sembrar los tipos y reintentar. Se corre con `python -m scripts.enrich_operaciones`.
Conecta con: invoca api.services.operaciones_informes.enriquecer; escribe CashFlow.Operaciones.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[core.mongo]]  ·  _module_
