---
id: tests.unit.test_notify
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_notify.py
---

# tests/unit/test_notify

> Tests del notificador de alertas (core/notify.py).

**Archivo:** `tests/unit/test_notify.py`

## Qué hace
Valida el notificador de alertas Telegram (`core/notify.py`): que sea no-op sin config (sin token/chat), que nunca propague un fallo de red al caller (devuelve False, no lanza) y que la alerta de fallo de job lleve metadata segura (nombre, status, referencia a Manager.JobRuns) con el último error truncado a 180 chars.

Conecta con: importa `core.notify`; red de seguridad de las notificaciones operativas que disparan los crons vía `core.job_runs`.

## Usa / conecta con →
- [[core.notify]]  ·  _module_
