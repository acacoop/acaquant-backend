---
id: svc.motor_ordenes
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/motor_ordenes.service
---

# systemd: motor_ordenes

> Servicio systemd.

**Archivo:** `deploy/systemd/motor_ordenes.service`

## Qué hace
Servicio systemd del motor de órdenes — corre `engines.motor_ordenes`, que escucha los execution/order reports de ROFEX y persiste el ciclo de vida de cada orden (OrdenesLive/Audit). Vive en rueda con horario propio: cron restart 13:30 UTC / stop 20:05 UTC (L-V).

Conecta con: ejecuta `engines/motor_ordenes.py`; usa la sesión de órdenes pyRofex (`core/rofex_orders_session.py`); persiste a Mongo el estado de órdenes que el router `/api/ordenes` envía. Controlado por cron.

## Usa / conecta con →
- [[engines.motor_ordenes]]  ·  _module_
