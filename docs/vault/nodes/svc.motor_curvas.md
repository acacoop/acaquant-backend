---
id: svc.motor_curvas
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/motor_curvas.service
---

# systemd: motor_curvas

> Servicio systemd.

**Archivo:** `deploy/systemd/motor_curvas.service`

## Qué hace
Servicio systemd del motor de curvas — corre `engines.curvas`, que enriquece en tiempo real cada trade de renta fija con TEA/TNA/Duration. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/curvas.py`; lee `Trading.Curvas` (shape de flujos) + market data live, escribe métricas a `Trading.MarketSnapshot.metrics` (TEA leída luego por forwards/breakevens/services). Controlado por cron.

## Usa / conecta con →
- [[engines.curvas]]  ·  _module_
