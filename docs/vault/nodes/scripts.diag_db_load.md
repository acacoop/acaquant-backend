---
id: scripts.diag_db_load
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_db_load.py
---

# scripts/diag_db_load

> scripts/diag_db_load.py — READ-ONLY: qué está exigiendo a Mongo AHORA.

**Archivo:** `scripts/diag_db_load.py`

## Qué hace
Diagnóstico read-only que se conecta al PRIMARY de Atlas y lista las operaciones Mongo activas ordenadas por tiempo corriendo, marcando los COLLSCAN (escaneos de colección completa, lo que suele clavar el CPU en el M10). También muestra conexiones y opcounters. Es la herramienta de primera línea ante un pico de CPU del cluster. Requiere privilegio `inprog` en el usuario Mongo; si no lo tiene, lo avisa. Se corre con python -m scripts.diag_db_load [--min-secs 1].
Conecta con: core.mongo (cliente rw al primary), serverStatus/currentOp de Mongo. Herramienta de incidentes de performance (RUNBOOK).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
