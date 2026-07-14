---
id: engines.motor_ordenes
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\motor_ordenes.py
---

# engines/motor_ordenes

> Motor de órdenes — escucha execution reports y persiste el ciclo de vida.

**Archivo:** `engines\motor_ordenes.py`

## Qué hace
Motor que escucha los execution reports del broker por WS (`order_report_subscription`) y persiste el ciclo de vida completo de cada orden, para que el frontend lea el estado desde Mongo sin esperar respuesta sincrónica. Al arrancar hace recovery: reconcilia órdenes no-finales contra el estado real del broker (cierra las completadas mientras estuvo caído, marca huérfanas).

Conecta con: upsert a `Operaciones.OrdenesLive` (estado vivo por cl_ord_id) y append a `Operaciones.OrdenesAudit` (cada ER/request crudo); usa `core.rofex_orders_session` (sesión pyRofex dedicada a órdenes). Lo invoca systemd `motor_ordenes.service`. Las órdenes las envía el API (`api.routers.ordenes` / `api.services.ordenes`); este motor solo escucha el ciclo de vida.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[core.rofex_orders_session]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_ordenes]]  ·  _service_
