---
id: db.Trading.TimeSales
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.TimeSales

> Colección Mongo en DB Trading.

## Qué hace
Stream de trades (time & sales) por instrumento en la base `Trading`, enriquecido en tiempo real con métricas de curva. Registro tick-a-tick que respalda series intradía y el último trade por ticker.

Conecta con: la escribe/enriquece `engines/curvas.py` (motor de enriquecimiento real-time); la leen `api/services/canje.py`, `carry_trade.py`, `cotizaciones.py` y validaciones del Manager. Para tasas live se prefiere `MarketSnapshot` (TimeSales agregado es más caro).

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[engines.breakevens]]  ·  _module_
- [[engines.valores]]  ·  _module_
- [[jobs.backfill_breakevens]]  ·  _module_
- [[jobs.backfill_forwards]]  ·  _module_
- [[jobs.cierre_canje]]  ·  _module_
