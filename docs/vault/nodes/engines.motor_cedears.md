---
id: engines.motor_cedears
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/motor_cedears.py
---

# engines/motor_cedears

> motor_cedears.py — feed live de CEDEARs vía pyRofex WS.

**Archivo:** `engines/motor_cedears.py`

## Qué hace
Feed live de CEDEARs por WS pyRofex, espejo reducido de motor_rofex. Universo: los CEDEARs ARS 24hs activos del master. Hace un arranque en frío por REST (seed inicial para que tickers ilíquidos no muestren cero) y luego en cada tick actualiza el estado en memoria, persistiendo open/high/low/close/last cada 1s.

Conecta con: lee el universo de `Trading.Cedears` (activo=True) y escribe a `Trading.CedearsSnapshot`; usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_cedears.service` (L-V 13-20 UTC). Alimenta el Scanner CEDEARs vía `api.services.scanner` / `api.routers.scanner`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.threads]]  ·  _module_
- [[core.websocket]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_cedears]]  ·  _service_
