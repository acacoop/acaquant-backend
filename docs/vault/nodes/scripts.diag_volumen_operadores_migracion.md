---
id: scripts.diag_volumen_operadores_migracion
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_volumen_operadores_migracion.py
---

# scripts/diag_volumen_operadores_migracion

> Diag READ-ONLY — verifica los supuestos de la fase 2 (migrar VOLUMEN de

**Archivo:** `scripts/diag_volumen_operadores_migracion.py`

## Qué hace
Diagnóstico read-only que valida los supuestos para migrar el volumen por operador desde NegocioMovimientos a CashFlow.Operaciones. Chequea tres cosas: que el MEP sea consistente por fecha (un valor por día), el signo del campo `bruto`, y el delta de volumen viejo vs nuevo por operador pesificando USD con el mapa {fecha: mep}. No escribe nada. Se corre con `python -m scripts.diag_volumen_operadores_migracion`.
Conecta con: lee CashFlow.NegocioMovimientos y CashFlow.Operaciones; respalda el cambio de fuente de volumen de api.services.comercial.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
