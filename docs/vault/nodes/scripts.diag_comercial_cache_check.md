---
id: scripts.diag_comercial_cache_check
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_comercial_cache_check.py
---

# scripts/diag_comercial_cache_check

> scripts/diag_comercial_cache_check.py — VERIFICA que el rollup == camino viejo.

**Archivo:** `scripts/diag_comercial_cache_check.py`

## Qué hace
Diagnóstico read-only que confirma que el rollup precomputado Clientes.ComercialCache da los mismos números que el camino vivo (informe_comercial con COLLSCAN), antes de migrar el service a leer del cache. Compara los grandes totales (vol_total, vol_mes, ar_total, ar_mes, n_ops); si matchean dentro de $1 / 1 op, el rollup es fiel. Prerequisito: correr antes jobs.comercial_rollup --full. Se corre con python -m scripts.diag_comercial_cache_check.
Conecta con: api.services.comercial.informe_comercial, Clientes.ComercialCache, jobs.comercial_rollup, core.mongo. Gate de seguridad de la migración a ComercialCache.

## Usa / conecta con →
- [[api.services.comercial]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Clientes.ComercialCache]]  ·  _collection_
