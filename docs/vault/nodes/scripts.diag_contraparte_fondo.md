---
id: scripts.diag_contraparte_fondo
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_contraparte_fondo.py
---

# scripts/diag_contraparte_fondo

> diag_contraparte_fondo.py — por qué una contraparte (Fondo) aparece o no en

**Archivo:** `scripts/diag_contraparte_fondo.py`

## Qué hace
Diagnóstico read-only que rastrea por qué una contraparte tipo Fondo aparece o no en la vista /operaciones (Contrapartes Fondo). La lista de fondos es una intersección por nombre exacto entre ContrapartesAPI (grupo='Fondos') y AssetsAPI (cartera FCI), y el dato viaja por una cadena de copias de dos pasos (Contrapartes → ContrapartesAPI intermedia → CuentasAPI.ContrapartesAPI final). El diag recorre las 4 etapas y dice en cuál se cae (típicamente: corriste 'contrapartes' pero no 'mover'). Se corre con python -m scripts.diag_contraparte_fondo [--nombre GALILEO].
Conecta con: CashFlow.Contrapartes, CuentasAPI.ContrapartesAPI, TitulosAPI.AssetsAPI, scripts.api_migrate, core.mongo. Debuggea el endpoint /fondos de operaciones.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Contrapartes]]  ·  _collection_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_
