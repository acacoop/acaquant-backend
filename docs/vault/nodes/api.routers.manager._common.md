---
id: api.routers.manager._common
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/_common.py
---

# api/routers/manager/_common

> Constantes y helpers compartidos entre los sub-módulos de manager/.

**Archivo:** `api/routers/manager/_common.py`

## Qué hace
Módulo de constantes/helpers compartidos entre los sub-routers de `manager/`. Define `_AR_TZ` (timezone America/Argentina/Buenos_Aires, para formatear timestamps en hora local) y `PROJECT_ROOT` (raíz del repo, usado como cwd del subprocess que dispara `jobs/run`).

Conecta con: lo importan `manager/jobs.py`, `manager/status.py` y `manager/options.py`. Sin lógica de negocio ni acceso a Mongo.

## Lo usan (backlinks) ←
- [[api.routers.manager.jobs]]  ·  _module_
- [[api.routers.manager.options]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
