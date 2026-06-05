---
id: db.Trading.Curvas
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# Trading.Curvas

> Colección Mongo en DB Trading.

## Qué hace
Definición de cada instrumento de renta fija y su flujo de fondos, en la base `Trading`. Fuente de verdad de los flujos (CER porcentual, tasa_fija absoluta, soberanos en USD) que alimentan toda la valuación: TEA/TNA/duration, breakevens, carry, sensibilidad. Shape crítico, no inferible (ver CLAUDE.md).

Conecta con: la consumen los motores (`engines/curvas.py`, breakevens, forwards) y casi todos los services de renta fija (`renta_fija.py`, `comparar_inversion.py`, `sinteticos.py`, `fair_value.py`). Une con `Valuaciones.Assets` por `ticker_corto == TICKER` para entrar al AuM. Se limpia con `jobs/cleanup_curvas.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[jobs.cleanup_curvas]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[scripts.api_migrate]]  ·  _module_
- [[scripts.crear_indices]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
- [[scripts.perf_sweep]]  ·  _module_
- [[tests.unit.test_cotizaciones_tier2]]  ·  _module_
