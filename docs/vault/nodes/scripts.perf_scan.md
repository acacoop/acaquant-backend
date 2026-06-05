---
id: scripts.perf_scan
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/perf_scan.py
---

# scripts/perf_scan

> perf_scan.py — Static analysis para anti-patterns de queries Mongo.

**Archivo:** `scripts/perf_scan.py`

## Qué hace
Herramienta reusable de análisis estático: escanea api/, engines/ y jobs/ buscando anti-patterns de queries Mongo (PERF001 find sin projection, PERF002 N+1 en loops, PERF003 count_documents({}) total, PERF004 query repetida cacheable). Reporta sin tocar la DB. Se suprime una línea con `# noqa: PERF00X`. Corre informativo (`python -m scripts.perf_scan`, exit 0) o `--strict` (exit 1 si hay findings) para CI/pre-deploy.

Conecta con: lo invoca el CI (.github/workflows/ci.yml, informativo) y la skill /perf; opera solo sobre el AST del código, no sobre Mongo.

_Sin conexiones detectadas mecánicamente._
