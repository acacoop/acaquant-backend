---
id: cron.jobs.comercial_warm
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.comercial_warm

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron cada 4 minutos en horario de mercado (13-20 UTC, L-V) que corre `jobs.comercial_warm`: precalienta la cache in-process de la vista COMERCIAL para que la API no la recompute en caliente. Timeout 3m (< intervalo) + lock para no apilarse.

Conecta con: ejecuta `jobs/comercial_warm.py`; pega al endpoint/servicio comercial (`api/services/comercial.py`) para llenar su cache. Lee Comitentes/AuM/NegocioMovimientos vía ese service.

## Usa / conecta con →
- [[jobs.comercial_warm]]  ·  _module_
