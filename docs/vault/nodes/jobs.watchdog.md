---
id: jobs.watchdog
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/watchdog.py
---

# jobs/watchdog

> jobs/watchdog.py — "el agente que evalúa solo": detecta jobs colgados y alerta.

**Archivo:** `jobs/watchdog.py`

## Qué hace
Job de visibilidad ("el agente que evalúa solo") que corre cada 5 min y escanea los procesos `python -m jobs.X` vivos en el Droplet. Si un job sigue corriendo tras superar su presupuesto (timeout de run_job.sh + margen) manda alerta a Telegram — una alerta significa que el kill automático falló. Aparte, best-effort, detecta queries Mongo que examinan mucho y devuelven poco (lo que Atlas no avisa por mail). Cooldown por job para no spamear.

Conecta con: lee procesos vía `ps`, slow queries vía `core.atlas_api`, alerta por `core.notify.send_telegram`, persiste cooldown en `Manager.WatchdogAlertas`. No mira los motores (engines) ni depende de JobRunLogger.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.atlas_api]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.notify]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.watchdog]]  ·  _cron_
