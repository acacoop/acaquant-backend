---
id: scripts.atlas_health
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/atlas_health.py
---

# scripts/atlas_health

> scripts/atlas_health.py — salud del M10 desde la Atlas Admin API (read-only).

**Archivo:** `scripts/atlas_health.py`

## Qué hace
Diagnóstico read-only de la salud del cluster Mongo M10 vía la Atlas Admin API. Imprime el CPU por nodo y las slow queries recientes, pero SOLO metadatos (colección, planSummary, docsExamined, duración) — nunca el comando ni valores, redacción por diseño. No toca el cluster. Sirve como paso de "medir antes de cablear el watchdog".
Se corre con `python -m scripts.atlas_health [--min 30]`.
Conecta con: `core.atlas_api` (cpu_por_nodo / slow_queries_meta); env vars `ATLAS_*` del `.env`.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.atlas_api]]  ·  _module_
