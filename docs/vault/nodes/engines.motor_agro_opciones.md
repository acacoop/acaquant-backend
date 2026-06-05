---
id: engines.motor_agro_opciones
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\motor_agro_opciones.py
---

# engines/motor_agro_opciones

> Motor de Opciones Agro Rosario — Trigo / Maíz / Soja.

**Archivo:** `engines\motor_agro_opciones.py`

## Qué hace
Motor de opciones agro de Rosario (Trigo/Maíz/Soja). Descubre las opciones sobre futuros Rosario filtrando por cficode (OCAFXS=call, OPAFXS=put) y parseando strike/vencimiento del symbol; las suscribe por WS con depth=1 y persiste un doc por ticker con bid/offer/last + strike + días a vto. Re-discovery cada 5 min.

Conecta con: escribe a `Trading.AgroOpcionesSnapshot` (ReplaceOne cada 5s); usa `core.rofex_session` + `core.websocket`. Lo invoca systemd `motor_agro_opciones.service` (L-V 13-20 UTC). Lo consume la vista de estrategias agro vía `api.services.derivados_agro`. No escribe TimeSales ni histórico de cierre.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[core.websocket]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_agro_opciones]]  ·  _service_
