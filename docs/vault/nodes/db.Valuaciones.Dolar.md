---
id: db.Valuaciones.Dolar
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Valuaciones.Dolar

> Colección Mongo en DB Valuaciones.

## Qué hace
Serie de dólar de referencia (MEP histórico) en la base `Valuaciones`. Provee el tipo de cambio para convertir valuaciones y operaciones a USD por fecha.

Conecta con: la lee el helper `api/services/_mep.py` (MEP histórico por fecha) y services como `carry_trade.py`, `scanner.py`, `valuaciones.py`, `operaciones_informes.py`; se ingesta vía `api/routers/ingest.py`.

## Lo usan (backlinks) ←
- [[api.services._mep]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.dolar_mep]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
