---
id: cron.jobs.cleanup_futuros_dlr
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: jobs.cleanup_futuros_dlr

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron L-V a 12:30 UTC (09:30 ART, antes de abrir los motores) que corre `jobs.cleanup_futuros_dlr`: purga los contratos DLR ya vencidos de `Trading.FuturosDLRSnapshot`.

Conecta con: ejecuta `jobs/cleanup_futuros_dlr.py`; escribe (delete) en `Trading.FuturosDLRSnapshot`, colección que alimenta motor_futuros_dlr. Timeout 10m.

## Usa / conecta con →
- [[jobs.cleanup_futuros_dlr]]  ·  _module_
