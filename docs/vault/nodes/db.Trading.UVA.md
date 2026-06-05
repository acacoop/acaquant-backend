---
id: db.Trading.UVA
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.UVA

> Colección Mongo en DB Trading.

## Qué hace
Valor de la UVA (carga manual) en la base `Trading`. Es la fuente del valor UVA usado para clasificar a las personas jurídicas en la segmentación patrimonial (umbrales PJ en UVAs).

Conecta con: la lee `jobs/segmentar_patrimonial.py`, `api/services/macro.py` y `api/routers/manager/comercial.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.comercial]]  ·  _module_
- [[api.services.macro]]  ·  _module_
