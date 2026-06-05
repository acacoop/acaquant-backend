---
id: svc.motor_forwards
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/motor_forwards.service
---

# systemd: motor_forwards

> Servicio systemd.

**Archivo:** `deploy/systemd/motor_forwards.service`

## Qué hace
Servicio systemd del motor de forwards — corre `engines.forwards`, que calcula la matriz de tasas forward implícitas entre instrumentos en tiempo real. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/forwards.py`; lee la última TEA por ticker desde `Trading.MarketSnapshot.metrics.TEA` (escrita por motor_curvas) y publica la matriz forward live. Controlado por cron.

## Usa / conecta con →
- [[engines.forwards]]  ·  _module_
