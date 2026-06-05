---
id: scripts.lock_deps
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/lock_deps.py
---

# scripts/lock_deps

> Genera un requirements.txt pineado a las versiones EXACTAS instaladas.

**Archivo:** `scripts/lock_deps.py`

## Qué hace
Herramienta reusable que regenera requirements.txt pineando cada dependencia a la versión EXACTA ya instalada en el venv (no resuelve contra PyPI, para no traer la última). Soluciona que el requirements no tenía pins: cada pip install podía traer una versión incompatible y tumbar el boot al restart. Deja dos bloques: directas (con sus comentarios) y transitivas pineadas. Con `--check` solo reporta sin escribir. Se corre con `python -m scripts.lock_deps [--check]` dentro del venv.
Conecta con: reescribe requirements.txt; mitiga el riesgo EXT-DEP1 de la auditoría de seguridad.

_Sin conexiones detectadas mecánicamente._
