---
id: cron.engines.dolar_mep
type: cron
layer: deploy
repo: infra
tags: [cron, deploy, infra]
path: deploy/crontab.txt
---

# cron: engines.dolar_mep

> Tarea programada (cron).

**Archivo:** `deploy/crontab.txt`

## Qué hace
Cron cada 15 minutos durante la rueda (13:00-20:45 UTC, L-V) que corre `engines.dolar_mep`: snapshot puntual de MEP + CCL. A diferencia de los motores WS always-on, este se invoca por cron como un pull periódico.

Conecta con: ejecuta `engines/dolar_mep.py`; lee precios de bonos y persiste el MEP/CCL a Mongo, consumido por la API. Timeout 5m.

## Usa / conecta con →
- [[engines.dolar_mep]]  ·  _module_
