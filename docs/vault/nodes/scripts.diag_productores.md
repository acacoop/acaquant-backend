---
id: scripts.diag_productores
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_productores.py
---

# scripts/diag_productores

> diag_productores.py — lista los PRODUCTORES (Clientes.Comitentes.nivel_1).

**Archivo:** `scripts/diag_productores.py`

## Qué hace
Diagnóstico read-only que lista los PRODUCTORES, es decir los comitentes cuya segmentación `nivel_1` es 'PRODUCTORES'. Es la fuente del filtro "Solo productores" de la vista Negocio → Movimientos, que cruza estos comitentes con los movimientos y el AuM por `id_cuenta` (no por el string `cuenta`). Muestra id_cuenta, titular, operador y nivel_2 de cada uno; si está vacío, ayuda a chequear el casing (los niveles van en MAYÚSCULAS). Se corre con python -m scripts.diag_productores.

Conecta con: Clientes.Comitentes (nivel_1), cliente Mongo de solo lectura. Espeja la lógica de api/services/_cuentas_filter._ids_cuenta_productores (antes la fuente era CashFlow.Productores).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
