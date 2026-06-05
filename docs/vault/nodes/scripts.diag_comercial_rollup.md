---
id: scripts.diag_comercial_rollup
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_comercial_rollup.py
---

# scripts/diag_comercial_rollup

> scripts/diag_comercial_rollup.py — MEDIR antes de diseñar ComercialCache (C1/C2).

**Archivo:** `scripts/diag_comercial_rollup.py`

## Qué hace
Diagnóstico read-only que MIDE lo que define el diseño de Clientes.ComercialCache antes de escribir el rollup: cuánto tardan los dos killers (informe_comercial y serie_comercial sobre toda la colección) en cache fría, explain() de los $match calientes (COLLSCAN vs IXSCAN), y la cardinalidad del grano candidato {fecha × id_cuenta} para saber cuántas filas tendría el cache. También lista los índices existentes. Se corre con python -m scripts.diag_comercial_rollup.
Conecta con: api.services.comercial, api.services._negocio_futuros, CashFlow.NegocioMovimientos, CashFlow.Operaciones, core.mongo. Insumo de diseño de ComercialCache.

## Usa / conecta con →
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
