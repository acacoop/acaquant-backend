---
id: tests.unit.test_argentina_datos
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_argentina_datos.py
---

# tests/unit/test_argentina_datos

> Tests del cliente argentinadatos + job de persistencia.

**Archivo:** `tests/unit/test_argentina_datos.py`

## Qué hace
Valida el cliente de argentinadatos.com y el job que persiste sus series. Por el lado cliente confirma que riesgo país / inflación mensual / interanual / REM parsean bien, que arman el path con zero-padding (`/rems/2026/03`) y que una respuesta con shape inesperado o status de error lanza `ArgDataError`. Por el lado job verifica que persiste las 3 series, que descarta valores anómalos (riesgo país fuera de rango), que `solo=` aísla una serie y que si una serie falla las otras igual se guardan y reporta `ok=False`.

Conecta con: blinda `core/argentina_datos.py` y `jobs/argentina_datos.py` (cron 12 UTC que alimenta RiesgoPais/IPC/REM); todo mockeado (`requests.get` + Mongo), sin red ni DB real.

## Usa / conecta con →
- [[core.argentina_datos]]  ·  _module_
- [[jobs.argentina_datos]]  ·  _module_
