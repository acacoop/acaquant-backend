---
id: svc.motor_options
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy\systemd\motor_options.service
---

# systemd: motor_options

> Servicio systemd.

**Archivo:** `deploy\systemd\motor_options.service`

## Qué hace
Servicio systemd del motor de opciones GGAL — corre `engines.options`, el feed live de la cadena de opciones de GGAL (precios + griegas). Vive solo en rueda (cron restart 13:00 UTC / stop 20:05 UTC, L-V).

Conecta con: ejecuta `engines/options.py`; suscribe los contratos de opciones vía pyRofex WS y escribe a `Opciones.Data`; los jobs `options_rollup` y `archive_options_data` consolidan/purgan esa colección. Controlado por cron.

## Usa / conecta con →
- [[engines.options]]  ·  _module_
