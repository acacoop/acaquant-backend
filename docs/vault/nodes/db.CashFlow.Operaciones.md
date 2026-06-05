---
id: db.CashFlow.Operaciones
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CashFlow.Operaciones

> Colección Mongo en DB CashFlow.

## Qué hace
Operaciones normalizadas e ingestadas desde los informes de Aunesa, en la base `CashFlow`. Es el registro canónico de operaciones para análisis y backfill (carga por CSV o por job).

Conecta con: la escriben `jobs/operaciones_informes.py` y `api/services/operaciones_informes.py` (normalización + ingesta idempotente); backfill manual por CSV vía `api/routers/manager/operaciones.py`. La leen routers de operaciones/operativa y el scoping de grupos (`_grupos_scope.py`).

## Lo usan (backlinks) ←
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[jobs.comercial_rollup]]  ·  _module_
- [[jobs.descubrir_cuentas]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.ops_rollup]]  ·  _module_
- [[scripts.audit_operaciones]]  ·  _module_
- [[scripts.backfill_commodity_operaciones]]  ·  _module_
- [[scripts.backfill_es_cierre_operaciones]]  ·  _module_
- [[scripts.backfill_fci_bruto]]  ·  _module_
- [[scripts.backfill_mep_operaciones]]  ·  _module_
- [[scripts.backfill_nivel3_operaciones]]  ·  _module_
- [[scripts.backfill_operaciones_csv]]  ·  _module_
- [[scripts.crear_indice_serie_aranceles]]  ·  _module_
- [[scripts.db_maintenance]]  ·  _module_
- [[scripts.diag_agro_otc]]  ·  _module_
- [[scripts.diag_aranceles_breakdown]]  ·  _module_
- [[scripts.diag_aranceles_operadores_migracion]]  ·  _module_
- [[scripts.diag_comercial_rollup]]  ·  _module_
- [[scripts.diag_dups_check]]  ·  _module_
- [[scripts.diag_fci_bilateral]]  ·  _module_
- [[scripts.diag_fci_fuente]]  ·  _module_
- [[scripts.diag_fci_historico]]  ·  _module_
- [[scripts.diag_fci_match_boleto]]  ·  _module_
- [[scripts.diag_fci_shape]]  ·  _module_
- [[scripts.diag_indice_boleto]]  ·  _module_
- [[scripts.diag_nivel3]]  ·  _module_
- [[scripts.diag_operadores_actividad]]  ·  _module_
- [[scripts.diag_perf_aranceles]]  ·  _module_
- [[scripts.diag_scope_cuenta]]  ·  _module_
- [[scripts.diag_volumen_bruto_cero]]  ·  _module_
- [[scripts.diag_volumen_operadores_migracion]]  ·  _module_
- [[scripts.drop_indice_serie_aranceles]]  ·  _module_
- [[scripts.drop_indices_redundantes_operaciones]]  ·  _module_
- [[scripts.fix_indice_boleto]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
- [[scripts.seed_tipos_operacion]]  ·  _module_
