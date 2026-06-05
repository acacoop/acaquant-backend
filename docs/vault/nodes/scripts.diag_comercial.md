---
id: scripts.diag_comercial
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_comercial.py
---

# scripts/diag_comercial

> scripts/diag_comercial.py — QA de la vista COMERCIAL (OPERACIONES).

**Archivo:** `scripts/diag_comercial.py`

## Qué hace
Diagnóstico read-only de QA de la vista Comercial (Operaciones). Cronometra cada endpoint del service con cache fría, corre explain() en las queries calientes de NegocioMovimientos/AuM marcando IXSCAN (índice OK) vs COLLSCAN (escaneo total = problema), y valida invariantes de consistencia (Σ por cuenta == total). Se corre tras git pull + backfill con python -m scripts.diag_comercial [--operador correo].
Conecta con: api.services.comercial (todos sus entrypoints), CashFlow.NegocioMovimientos, Valuaciones.AuM, core.mongo. Red de seguridad de números + performance del Tablero Comercial.

## Usa / conecta con →
- [[api.services.comercial]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
