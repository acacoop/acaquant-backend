---
id: scripts.diag_flujo_contraparte
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_flujo_contraparte.py
---

# scripts/diag_flujo_contraparte

> diag_flujo_contraparte.py — por qué una contraparte con operaciones no

**Archivo:** `scripts/diag_flujo_contraparte.py`

## Qué hace
Diagnóstico read-only que explica por qué una contraparte con operaciones no aparece en la vista Contrapartes. Esa vista arma su lista agrupando por el campo `contraparte` de los flujos en OperacionesAPI.MesaAPI (copia de CashFlow.Flujo); el script busca los flujos que matchean un término en ambas colecciones y muestra qué `contraparte` tienen, para distinguir si el problema es de sync (no llegó a MesaAPI) o de linkeo (el nombre no coincide). Default busca "DRACMA". Se corre con python -m scripts.diag_flujo_contraparte [--q termino].

Conecta con: OperacionesAPI.MesaAPI (lo que lee la vista) y CashFlow.Flujo (fuente), cliente Mongo de solo lectura. Soporte a la vista /operaciones Contrapartes.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
