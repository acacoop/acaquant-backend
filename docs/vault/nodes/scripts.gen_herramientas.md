---
id: scripts.gen_herramientas
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/gen_herramientas.py
---

# scripts/gen_herramientas

> gen_herramientas.py — genera/actualiza docs/HERRAMIENTAS.md desde scripts/.

**Archivo:** `scripts/gen_herramientas.py`

## Qué hace
Generador auto-mantenible que regenera la tabla de docs/HERRAMIENTAS.md (el catálogo de scripts reusables del repo) escaneando los docstrings de scripts/*.py: cada script reusable se auto-declara con una línea `Herramienta: <categoría> · <descripción>`. Reescribe solo entre los marcadores AUTOGEN; la narrativa se mantiene a mano. Con `--check` falla si quedó desincronizado (uso en CI). Se corre con `python -m scripts.gen_herramientas [--check]`.
Conecta con: escanea scripts/; genera docs/HERRAMIENTAS.md (hermano de gen_sistema / gen_obsidian).

_Sin conexiones detectadas mecánicamente._
