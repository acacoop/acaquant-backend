---
id: scripts.diag_contrapartes_ids
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_contrapartes_ids.py
---

# scripts/diag_contrapartes_ids

> Diag READ-ONLY: qué colección de Contrapartes usar para "id_cuenta en

**Archivo:** `scripts/diag_contrapartes_ids.py`

## Qué hace
Diagnóstico read-only que compara las dos colecciones candidatas de contrapartes (CashFlow.Contrapartes vs CuentasAPI.ContrapartesAPI) para decidir cuál usar en la regla "id_cuenta en Contrapartes → PJ GRANDE" de la segmentación. Inspecciona campos, cantidad de ids distintos y overlap con las Comitentes activas, para no asumir cuál es la buena. No escribe nada. Se corre con python -m scripts.diag_contrapartes_ids.
Conecta con: CashFlow.Contrapartes, CuentasAPI.ContrapartesAPI, Clientes.Comitentes, core.mongo. Insumo de diseño de api.services.segmentacion.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Contrapartes]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_
