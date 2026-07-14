---
id: api.routers.manager.jobs
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\manager\jobs.py
---

# api/routers/manager/jobs

> POST /jobs/run + GET /jobs/history, /jobs/history/stats, /jobs/{id}.

**Archivo:** `api\routers\manager\jobs.py`

## Qué hace
Sub-router `/api/manager/jobs` — disparo manual de jobs batch desde el panel y consulta de su historial. `POST /jobs/run` lanza un subprocess `python -m jobs.<x>` (whitelist cerrada de comandos, timeout 360s, rate-limited 5/h) en un thread daemon y devuelve un job_id consultable. `GET /jobs/history` y `/jobs/history/stats` leen las corridas registradas. El catch-all `/jobs/{id}` va declarado al final para no tapar las rutas estáticas.

Conecta con: lanza subprocesos de `jobs.*` y `scripts.crear_indices` (cwd = PROJECT_ROOT de `_common`); lee `Manager.JobRuns` (TTL 60d, lo escriben los jobs vía `core.job_runs`); usa `api.ratelimit`. Lo consume la tab JOBS de la manager-view.

## Usa / conecta con →
- [[api.ratelimit]]  ·  _module_
- [[api.routers.manager._common]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.jobs_catalogo]]  ·  _module_
- [[api.services.manager_infra_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
- [[web.cmp.jobs-runs-panel]]  ·  _component_
- [[web.cmp.manager-controles-panel]]  ·  _component_
- [[web.cmp.manager-jobs-panel]]  ·  _component_
- [[web.cmp.manager-view]]  ·  _component_
