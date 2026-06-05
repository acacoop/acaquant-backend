---
id: db.Trading.DOLAR
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.DOLAR

> Colección Mongo en DB Trading.

## Qué hace
Serie histórica del dólar oficial/mayorista (fixing diario BCRA A3500) en la base `Trading`. Es la fuente para las series macro (`dolar_oficial` / `dolar_mayorista`) cuando se necesita histórico, a diferencia del feed live.

Conecta con: la escribe `jobs/bcra.py`; la leen `api/services/macro.py`, `carry_trade.py` y los motores (`engines/curvas.py`, `futuros_dlr.py`) que necesitan el oficial histórico.

## Lo usan (backlinks) ←
- [[api.routers.manager.status]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
- [[jobs.bcra]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
