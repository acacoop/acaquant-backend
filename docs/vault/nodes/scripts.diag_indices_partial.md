---
id: scripts.diag_indices_partial
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_indices_partial.py
---

# scripts/diag_indices_partial

> scripts/diag_indices_partial.py — busca OTROS índices partial que puedan causar

**Archivo:** `scripts/diag_indices_partial.py`

## Qué hace
Diagnóstico read-only que busca, en las bases grandes, otros índices PARTIAL que puedan causar COLLSCAN silenciosos como el de boleto (incidente 2026-06-04). Un índice partial solo se usa si la query incluye su partialFilterExpression; si la app consulta por igualdad sin ese filtro, hay scan oculto. Lista todos los índices partial con su filtro, marcando como peligrosos los UNIQUE partial sobre campos de ID consultados por igualdad. No escribe nada. Se corre con python -m scripts.diag_indices_partial.

Conecta con: bases CashFlow, Valuaciones, Trading, Clientes, Manager, CuentasAPI (list_indexes), cliente Mongo de solo lectura. Prevención del patrón del incidente uq_boleto.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
