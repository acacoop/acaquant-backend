---
id: svc.motor_agro
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/motor_agro.service
---

# systemd: motor_agro

> Servicio systemd.

**Archivo:** `deploy/systemd/motor_agro.service`

## Qué hace
Servicio systemd del motor de futuros agro — corre `engines.motor_agro`, el feed live de futuros de Trigo / Maíz / Soja en Rosario (contratos FXXXSX). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/motor_agro.py`; suscribe los futuros agro vía pyRofex WS y publica precios live consumidos por el módulo `/api/derivados/agro` (Pase Agro, estrategias). Controlado por cron.

## Usa / conecta con →
- [[engines.motor_agro]]  ·  _module_
