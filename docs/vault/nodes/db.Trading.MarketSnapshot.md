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
