---
id: jobs.cleanup_curvas
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\cleanup_curvas.py
---

# jobs/cleanup_curvas

> Limpieza de instrumentos vencidos en mercado.curvas (SQL-native).

**Archivo:** `jobs\cleanup_curvas.py`

## Qué hace
Borra de `Trading.Curvas` los instrumentos que vencen a menos de 2 días hábiles, usando el calendario hábil argentino. Evita que bonos/letras ya vencidos sigan apareciendo en las curvas y motores. Tiene modo `--dry` que solo lista lo que borraría.

Corre cada mañana antes de que arranquen los motores de mercado (cadencia análoga a `cleanup_futuros_dlr`).

Conecta con: lee `Trading.DiasHabiles` (calendario) y `Trading.Curvas`, borra docs vencidos de `Trading.Curvas`. Depende de `jobs.dias_habiles` (si el calendario está vacío, aborta).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.calendario]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.cleanup_curvas]]  ·  _cron_
