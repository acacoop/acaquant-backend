---
id: web.cmp.jobs-runs-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\jobs-runs-panel.tsx
---

# web/components/jobs-runs-panel

**Archivo:** `src\components\jobs-runs-panel.tsx`

## Qué hace
Panel de monitoreo de corridas de jobs/crons batch: lista los runs recientes (estado ok/partial/error, duración, stats, errores, log) y estadísticas agregadas por tipo de job, con filtros por tipo y estado.

Conecta con: fetch a los endpoints de jobs del Manager (`/api/manager/jobs/history`, `/jobs/history/stats`) que leen `Manager.JobRuns` (poblada por `core.job_runs`). Se monta como tab dentro de `manager-view`.

## Usa / conecta con →
- [[api.routers.manager.jobs]]  ·  _module_
