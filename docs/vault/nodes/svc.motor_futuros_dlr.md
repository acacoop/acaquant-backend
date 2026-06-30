---
id: svc.motor_futuros_dlr
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/motor_futuros_dlr.service
---

# systemd: motor_futuros_dlr

> Servicio systemd.

**Archivo:** `deploy/systemd/motor_futuros_dlr.service`

## Qué hace
Servicio systemd del motor de futuros DLR — corre `engines.futuros_dlr`, que arma la curva de futuros de Dólar A3500 (outrights single-leg) con su tasa implícita. Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/futuros_dlr.py`; suscribe los contratos DLR vía pyRofex WS y escribe a `Trading.FuturosDLRSnapshot`; el job `cleanup_futuros_dlr` purga contratos vencidos. Controlado por cron.

## Usa / conecta con →
- [[engines.futuros_dlr]]  ·  _module_
