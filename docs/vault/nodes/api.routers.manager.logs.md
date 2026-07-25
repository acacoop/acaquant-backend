---
id: api.routers.manager.logs
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/logs.py
---

# api/routers/manager/logs

> GET /api/manager/logs — últimos N logs de un servicio systemd.

**Archivo:** `api/routers/manager/logs.py`

## Qué hace
Sub-router `/api/manager/logs` — devuelve los últimos N logs de un servicio systemd corriendo `journalctl -u <svc>.service --output=json` como subprocess y parseando el JSON (timestamp + priority + message). Whitelist estricta de servicios permitidos (anti-RCE), líneas acotadas a [1,500], cache TTL 2s. Admin-only.

Conecta con: ejecuta `journalctl` en el host del Droplet; usa `api.cache::cached`. No toca Mongo. Lo consume la tab LOGS de la manager-view para dashboards operativos.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
- [[web.cmp.logs-panel]]  ·  _component_
