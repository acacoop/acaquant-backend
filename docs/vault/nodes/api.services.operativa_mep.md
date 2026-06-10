---
id: api.services.operativa_mep
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/operativa_mep.py
---

# api/services/operativa_mep

> Operativa Dólar MEP — wrapper de 2 órdenes MARKET (BUY AL30 + SELL AL30D).

**Archivo:** `api/services/operativa_mep.py`

## Qué hace
Ejecuta la operativa "Dólar MEP" como wrapper de dos órdenes MARKET: BUY AL30 (en ARS) + SELL AL30D (en USD), sobre la misma especie en distintas ruedas. El cliente entra pesos y sale dólares MEP. No es atómica: entre pata y pata el precio puede moverse, pero AL30/AL30D son ultra-líquidos. Registra estado y resultado de ambas patas.

Conecta con: lee la cotización live del último trade en `Trading.TimeSales`, manda las órdenes vía `api.services.ordenes.send_order` (que escribe en `Operaciones.OrdenesLive`), y persiste el wrapper en `Operaciones.OperativasMep`. Lo invoca el router `/api/operativa`.

## Usa / conecta con →
- [[api.services.ordenes]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.operativa]]  ·  _module_
