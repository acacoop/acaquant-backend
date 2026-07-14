---
id: jobs
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\__init__.py
---

# jobs/__init__

**Archivo:** `jobs\__init__.py`

## Qué hace
Paquete `jobs/` — los procesos batch/cron del sistema (no se importan como librería, cada uno corre con `python -m jobs.<x>`). El `__init__.py` está vacío: es solo el marcador de paquete. La capa `jobs/` usa `core/` + `quant/` y normalmente no importa de `api/` (salvo precomputes que reusan un service).

Conecta con: el cron del Droplet (`deploy/crontab.txt`) dispara cada job; horarios y encadenamientos viven ahí.

_Sin conexiones detectadas mecánicamente._
