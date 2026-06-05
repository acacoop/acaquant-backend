---
id: cron.jobs.aum
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.aum

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V en 5 corridas (15/17/18:30/21/23 UTC) que corre `jobs.aum` y encadena `sync_api_copies --aum --titulos --assets`: snapshot del AuM (activos bajo administración) por cuenta. Idempotente: borra el snapshot de hoy por cuenta antes del bulk write.

Conecta con: ejecuta `jobs/aum.py` + `jobs/sync_api_copies.py`; lee `Trading.Curvas`/`Valuaciones.Assets`/precios y escribe a `Valuaciones.AuM`; propaga a las copias API. Timeout 25m.

## Usa / conecta con →
- [[jobs.aum]]  ·  _module_
