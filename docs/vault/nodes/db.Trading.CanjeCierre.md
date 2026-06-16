---
id: db.Trading.CanjeCierre
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.CanjeCierre

> Colección Mongo en DB Trading.

## Qué hace
Cierre diario de los tickers de canje (CCL/MEP intra-bono, ej. AL30C/AL30D) en la base `Trading`. Materializa el valor de cierre para construir la serie histórica del canje sin recalcular desde trades.

Conecta con: la escribe `jobs/cierre_canje.py` (post-cierre); la lee `api/services/canje.py` para la serie histórica del canje.

## Lo usan (backlinks) ←
- [[api.services.canje]]  ·  _module_
- [[api.services.diagnostico_registry]]  ·  _module_
- [[jobs.cierre_canje]]  ·  _module_
- [[jobs.sync_postgres]]  ·  _module_
