---
id: scripts.diag_index_usage
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_index_usage.py
---

# scripts/diag_index_usage

> diag_index_usage.py — uso real de cada índice ($indexStats), read-only.

**Archivo:** `scripts/diag_index_usage.py`

## Qué hace
Herramienta DBA reusable (read-only) que mide el uso real de cada índice vía $indexStats: por colección lista cada índice con sus `ops` (cuántas veces lo eligió el planner desde el último restart del nodo), su tamaño y su key. Marca los MUERTOS (0 ops, candidatos a dropear) y los redundantes (key prefijo de otro compuesto). Sirve para decidir qué índices borrar antes de crear nuevos. No modifica nada. Se corre con python -m scripts.diag_index_usage [--all] [--db Nombre].

Conecta con: todas las bases Mongo del cluster ($indexStats por colección), cliente de solo lectura. Complementa scripts.audit_db en la Fase 1 de DBA.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
