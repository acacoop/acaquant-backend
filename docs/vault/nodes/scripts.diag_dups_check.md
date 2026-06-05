---
id: scripts.diag_dups_check
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_dups_check.py
---

# scripts/diag_dups_check

> scripts/diag_dups_check.py — GARANTÍA de no-duplicados (regla dura del usuario).

**Archivo:** `scripts/diag_dups_check.py`

## Qué hace
Diagnóstico read-only que garantiza la regla dura de no-duplicados en CashFlow.Operaciones y CashFlow.NegocioMovimientos. Confirma dos cosas: que existe el índice ÚNICO que hace estructuralmente imposible el duplicado (Operaciones por `boleto`, NegocioMovimientos por fecha+comprobante), y que no hay duplicados ahora mismo (por si el índice se creó sobre datos sucios). Si falta el índice o aparecen dups, avisa que hay que limpiar antes de crearlo. Se corre con python -m scripts.diag_dups_check.
Conecta con: CashFlow.Operaciones, CashFlow.NegocioMovimientos (índices + lectura), core.mongo. Red de seguridad de integridad de boletos.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
