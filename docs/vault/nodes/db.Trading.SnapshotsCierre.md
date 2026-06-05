---
id: db.Trading.SnapshotsCierre
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.SnapshotsCierre

> Colección Mongo en DB Trading.

## Qué hace
Cierre diario por bono en la base `Trading`: persiste precio y métricas de cierre por instrumento, post-cierre de mercado. Es el histórico de cierres que evita recomputar desde trades y sirve para PnL, carry y descomposición.

Conecta con: la escribe el cron `jobs/snapshot_cierre.py` (lee `MarketSnapshot` y materializa el cierre); la leen `api/services/pnl.py`, `carry_trade.py`, `renta_fija.py`, `analitica.py` y `jobs/consolidado_cuentas.py`.

## Lo usan (backlinks) ←
- [[api.services.analitica]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[jobs.fair_value]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
