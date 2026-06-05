---
id: scripts.diag_perf_aranceles
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_perf_aranceles.py
---

# scripts/diag_perf_aranceles

> Diag READ-ONLY de performance del endpoint /ops/aranceles.

**Archivo:** `scripts/diag_perf_aranceles.py`

## Qué hace
Diagnóstico read-only de performance del endpoint /ops/aranceles. Mide con datos si el primer load de la serie de aranceles es lento y por qué: wall-clock del pipeline $facet completo (modo ÚLTIMA, ARS y USD, varias corridas), explain(executionStats) del sub-pipeline de la serie (COLLSCAN vs IXSCAN, docs/keys examinados) y la proporción real en CashFlow.Operaciones de total vs futuros vs futuros agro. No escribe nada. Se corre con python -m scripts.diag_perf_aranceles [--runs N].

Conecta con: CashFlow.Operaciones, cliente Mongo de solo lectura; replica el pipeline de ops_aranceles del router. Diagnostica la latencia de la vista de aranceles.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
