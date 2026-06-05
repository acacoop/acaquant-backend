---
id: scripts.diag_nivel3
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_nivel3.py
---

# scripts/diag_nivel3

> scripts/diag_nivel3.py — READ-ONLY: por qué `nivel_3` está vacío en Operaciones.

**Archivo:** `scripts/diag_nivel3.py`

## Qué hace
Diagnóstico read-only que explica por qué `nivel_3` (y `segmento`) están vacíos en CashFlow.Operaciones. Esos campos salen del enrich que joinea Operaciones.cuenta → Clientes.Comitentes.id_cuenta. Clasifica las cuentas con nivel_3 vacío en tres baldes: el Comitente no tiene nivel_3 cargado (falta segmentar), el Comitente sí lo tiene pero el doc no se re-enriqueció (un backfill lo arregla), o la cuenta no está en Comitentes (propias/FCI, nunca tendrá nivel_3). No escribe nada. Se corre con python -m scripts.diag_nivel3.

Conecta con: CashFlow.Operaciones y Clientes.Comitentes, cliente Mongo de solo lectura. Decide si hace falta segmentar o re-enriquecer.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
