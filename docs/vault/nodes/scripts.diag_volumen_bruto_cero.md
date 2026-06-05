---
id: scripts.diag_volumen_bruto_cero
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_volumen_bruto_cero.py
---

# scripts/diag_volumen_bruto_cero

> Diag READ-ONLY — ¿qué son los docs con bruto=0 en Operaciones y cuál es el gap

**Archivo:** `scripts/diag_volumen_bruto_cero.py`

## Qué hace
Diagnóstico read-only que investiga los ~300k docs con bruto=0 en CashFlow.Operaciones de cara a migrar el "volumen por operador" a esa colección. Desglosa por mercado/operación los bruto=0 (hipótesis: son futuros, sin importe en pesos) y los bruto>0 (lo que sí es volumen), y mide el gap de MEP para pesificar las ops en USD. No escribe nada. Se corre con `python -m scripts.diag_volumen_bruto_cero`.
Conecta con: lee CashFlow.Operaciones y CashFlow.NegocioMovimientos; sostiene la migración de volumen de api.services.comercial.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
