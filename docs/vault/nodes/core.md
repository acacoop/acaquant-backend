---
id: core
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/__init__.py
---

# core/__init__

**Archivo:** `core/__init__.py`

## Qué hace
Paquete `core/` — capa de infraestructura del backend. Agrupa los clientes externos (Aunesa, BYMA, Finnhub, Yahoo, argentinadatos, MAE, Atlas), el acceso a Mongo y los helpers transversales (roles, grupos, job_runs, snapshot_writer, websocket). Su `__init__.py` está vacío: solo marca el paquete.

Regla dura: `core/` no importa nada del resto del proyecto salvo `config` — es la base sobre la que se apoyan `engines/`, `jobs/` y `api/services/`.

Conecta con: lo importan engines, jobs y api/services; no depende de ellos.

## Lo usan (backlinks) ←
- [[api.services.aunesa_informes]]  ·  _module_
- [[api.services.compliance]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.watchdog]]  ·  _module_
- [[scripts.atlas_health]]  ·  _module_
- [[scripts.diag_aranceles_sin_match]]  ·  _module_
- [[scripts.diag_persona_datos]]  ·  _module_
