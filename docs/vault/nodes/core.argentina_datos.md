---
id: core.argentina_datos
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/argentina_datos.py
---

# core/argentina_datos

> Cliente de argentinadatos.com — indicadores macro AR públicos.

**Archivo:** `core/argentina_datos.py`

## Qué hace
Cliente HTTP del sitio público argentinadatos.com — indicadores macro argentinos sin auth: riesgo país (EMBI+, serie diaria + último), inflación IPC mensual e interanual, y el REM del BCRA (lista de meses + informe más reciente o por mes). Solo trae datos crudos; los rangos de sanity los aplican los jobs, no este cliente.

Conecta con: lo consume `jobs/argentina_datos.py` (cron 12 UTC) que persiste RiesgoPais / IPC / REM en Mongo; pega vía GET a `api.argentinadatos.com`.

## Lo usan (backlinks) ←
- [[jobs.argentina_datos]]  ·  _module_
