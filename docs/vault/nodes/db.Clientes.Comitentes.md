---
id: db.Clientes.Comitentes
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Clientes.Comitentes

> Colección Mongo en DB Clientes.

## Qué hace
Maestro de cuentas comitentes (clientes) en la base `Clientes`. Fuente de verdad de QUIÉN es cada cuenta: operador asignado, niveles `nivel_1..5` (en MAYÚSCULAS), `nivel_3` manual y el campo derivado `segmento_patrimonial`. Núcleo del Tablero Comercial y de la segmentación patrimonial.

Conecta con: la sincroniza desde Aunesa `jobs/sync_comitentes.py`; escribe segmentación `api/services/segmentacion.py` y `jobs/segmentar_patrimonial.py`. La leen `comercial.py`, `compliance.py`, `_cuentas_filter.py` y se edita vía `api/routers/manager/clientes.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.comercial]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.compliance]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.segmentar_patrimonial]]  ·  _module_
- [[jobs.sync_comitentes]]  ·  _module_
