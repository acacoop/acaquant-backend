---
id: db.CashFlow.NegocioMovimientos
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CashFlow.NegocioMovimientos

> Colección Mongo en DB CashFlow.

## Qué hace
Movimientos de negocio (boletos parseados, categorizados y agrupados) en la base `CashFlow`. Es la base de la vista `/operaciones/negocio` y del Tablero Comercial: registra cada operación con su `id_cuenta` denormalizado e indexado (nunca regex sobre `cuenta`), montos, arancel y categoría.

Conecta con: la escribe el cron `jobs/negocio_movimientos.py` (cada hora, pega a Aunesa); aranceles los completa `jobs/aranceles.py` / `api/services/aunesa_aranceles.py`. La leen `api/services/comercial.py`, `back_office_titulos.py` y los routers de operaciones. Filtros de exclusión en `_negocio_futuros.py`, `_negocio_arancelables.py`, `_negocio_informacion_filter.py`.

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services.aunesa_aranceles]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.comercial_rollup]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[scripts.audit_operaciones]]  ·  _module_
- [[scripts.backfill_fci_bruto]]  ·  _module_
- [[scripts.backfill_id_cuenta_negocio]]  ·  _module_
- [[scripts.cleanup_negocio_informacion]]  ·  _module_
- [[scripts.diag_actividad_mensual]]  ·  _module_
- [[scripts.diag_aranceles_breakdown]]  ·  _module_
- [[scripts.diag_aranceles_operadores_migracion]]  ·  _module_
- [[scripts.diag_aranceles_sin_match]]  ·  _module_
- [[scripts.diag_categoria_otro]]  ·  _module_
- [[scripts.diag_comercial]]  ·  _module_
- [[scripts.diag_comercial_rollup]]  ·  _module_
- [[scripts.diag_dups_check]]  ·  _module_
- [[scripts.diag_fci_bilateral]]  ·  _module_
- [[scripts.diag_fci_fuente]]  ·  _module_
- [[scripts.diag_fci_historico]]  ·  _module_
- [[scripts.diag_fci_job_perf]]  ·  _module_
- [[scripts.diag_fci_match_boleto]]  ·  _module_
- [[scripts.diag_fci_shape]]  ·  _module_
- [[scripts.diag_negocio_ruido]]  ·  _module_
- [[scripts.diag_operadores_actividad]]  ·  _module_
- [[scripts.diag_scope_cuenta]]  ·  _module_
- [[scripts.diag_sin_operador]]  ·  _module_
- [[scripts.diag_volumen_bruto_cero]]  ·  _module_
- [[scripts.diag_volumen_operadores_migracion]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
- [[scripts.perf_sweep]]  ·  _module_
- [[tests.integration.test_comercial_integration]]  ·  _module_
