---
id: db.Valuaciones.Assets
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Valuaciones.Assets

> Colección Mongo en DB Valuaciones.

## Qué hace
Catálogo de activos valuables en la base `Valuaciones`: cada `TICKER` con su `unidad` y tipo. Es el eslabón que conecta los instrumentos de mercado con las tenencias del AuM (un instrumento sin doc acá no aparece en portfolios/AuM).

Conecta con: une `Trading.Curvas.ticker_corto` → `Assets.TICKER` → `AuM.unidad` (cadena del AuM). La leen `api/services/portfolio.py`, `valuaciones.py`, `pnl.py`, `engines/_universo_portfolio.py`; se administra vía `api/routers/manager/assets.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.ons]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.titulos_flujos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_
- [[jobs.aum]]  ·  _module_
- [[jobs.aum_backfill]]  ·  _module_
- [[jobs.aum_backfill_historico]]  ·  _module_
- [[jobs.aum_resumen_fci]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.sync_postgres]]  ·  _module_
