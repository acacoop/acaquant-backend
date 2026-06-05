---
id: db.Trading.MarketSnapshot
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.MarketSnapshot

> Colección Mongo en DB Trading.

## Qué hace
Snapshot live por ticker en la base `Trading`: último precio, bid/ask y métricas calculadas (`metrics.TEA`, TNA, duration). Es el estado de mercado en tiempo real que escriben los motores y leen los endpoints "live fallback".

Conecta con: la escriben `motor_curvas` y demás motores vía `core/snapshot_writer.py`; la leen forwards, breakevens, renta_fija, sinteticos, cotizaciones y el MCP server. Para tasas/forwards se lee de acá la última TEA (no de TimeSales agregado).

## Lo usan (backlinks) ←
- [[api.routers.manager.status]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines.breakevens]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.forwards]]  ·  _module_
- [[engines.valores]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
