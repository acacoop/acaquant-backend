---
id: engines.motor_agro
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines/motor_agro.py
---

# engines/motor_agro

> Motor de Futuros Agro Rosario — Trigo / Maíz / Soja.

**Archivo:** `engines/motor_agro.py`

## Qué hace
Motor de futuros agro de Rosario (Trigo/Maíz/Soja). Descubre los outrights single-leg (cficode FXXXSX) cuyos underlyings matchean "Trigo/Maíz/Soja Rosario" (matcher tolerante a tildes), los suscribe por WS y persiste el último precio + puntas + días a vto. Re-discovery cada 5 min.

Conecta con: escribe a `Trading.AgroSnapshot` (ReplaceOne cada 5s); usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_agro.service` (L-V 13-20 UTC). Alimenta la vista PASE AGRO vía `api.services.derivados_agro`. No escribe TimeSales ni histórico de cierre.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.websocket]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_agro]]  ·  _service_
