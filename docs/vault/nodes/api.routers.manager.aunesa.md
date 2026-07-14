---
id: api.routers.manager.aunesa
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\manager\aunesa.py
---

# api/routers/manager/aunesa

> Manager · Aunesa — endpoint exploratorio en vivo.

**Archivo:** `api\routers\manager\aunesa.py`

## Qué hace
Sub-router `/api/manager/aunesa` — tab exploratoria/operativa contra el custodio Aunesa. Permite pegar EN VIVO a Aunesa (consolidado de boletos, posición valuada cruda de una cuenta), listar boletos sin arancel en un rango (tab FALTANTES) y disparar el backfill de aranceles en background (thread daemon del proceso api, progreso persistido por cuenta, marca `stale` si el proceso se reinicia). Admin-only; uso de discovery/debug — la vista de producción no usa estos endpoints.

Conecta con: services `aunesa_negocio` (fetch live), `aunesa_aranceles::run_backfill`, y el cliente Aunesa de `jobs.aum` (import diferido); lee `CashFlow.NegocioMovimientos` y persiste el estado del job en `Manager.AraneelesJobRuns`. Lo consume la manager-view.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services._negocio_arancelables]]  ·  _module_
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.aunesa_aranceles]]  ·  _module_
- [[api.services.aunesa_negocio]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[jobs.aum]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
- [[web.cmp.aunesa-boletos-panel]]  ·  _component_
- [[web.cmp.aunesa-explorar-panel]]  ·  _component_
- [[web.cmp.aunesa-posicion-panel]]  ·  _component_
