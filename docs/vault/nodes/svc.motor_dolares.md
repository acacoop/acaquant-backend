---
id: svc.motor_dolares
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy\systemd\motor_dolares.service
---

# systemd: motor_dolares

> Servicio systemd.

**Archivo:** `deploy\systemd\motor_dolares.service`

## Qué hace
Servicio systemd del motor de dólares — corre `engines.dolares`, que calcula MEP / CCL / canje en tiempo real vía WebSocket. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/dolares.py`; suscribe los pares de bonos (ej. AL30/AL30D) vía pyRofex WS y publica los dólares implícitos live consumidos por la API. Controlado por cron.

## Usa / conecta con →
- [[engines.dolares]]  ·  _module_
