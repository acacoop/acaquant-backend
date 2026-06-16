---
id: svc.motor_caucion
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy\systemd\motor_caucion.service
---

# systemd: motor_caucion

> Servicio systemd.

**Archivo:** `deploy\systemd\motor_caucion.service`

## Qué hace
Servicio systemd del motor de caución — corre `engines.caucion`, que sigue en tiempo real la TNA de caución ARS y USD a corto plazo (1 día; viernes 3 días). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/caucion.py`; suscribe los instrumentos de caución vía pyRofex WS y publica las tasas live consumidas por el módulo repo/caución de la API. Controlado por cron.

## Usa / conecta con →
- [[engines.caucion]]  ·  _module_
