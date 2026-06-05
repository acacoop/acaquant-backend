---
id: scripts.fix_indice_boleto
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/fix_indice_boleto.py
---

# scripts/fix_indice_boleto

> scripts/fix_indice_boleto.py — recrea el índice de boleto para que el upsert lo USE.

**Archivo:** `scripts/fix_indice_boleto.py`

## Qué hace
One-shot que arregla el root cause de un pico de CPU al 100%: el índice único `uq_boleto` era PARCIAL, y el upsert de operaciones hacía find({boleto}) a secas sin el partialFilterExpression, así que Mongo no lo usaba y escaneaba 488k docs por boleto. Crea un índice único PLANO sobre boleto, valida que el find ahora hace IXSCAN y recién entonces dropea el partial (sin ventana sin restricción única). Por defecto solo chequea; con `--fix` aplica (construir el índice escanea 488k una vez → fuera de rueda). Se corre con `python -m scripts.fix_indice_boleto [--fix]`.
Conecta con: opera sobre CashFlow.Operaciones; cura el upsert de api.services.operaciones_informes.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
