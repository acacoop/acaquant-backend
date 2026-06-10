---
id: api.services.aunesa_aranceles
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/aunesa_aranceles.py
---

# api/services/aunesa_aranceles

> Backfill de aranceles desde Aunesa /operaciones/informes a CashFlow.NegocioMovimientos.

**Archivo:** `api/services/aunesa_aranceles.py`

## Qué hace
Lógica core del backfill de aranceles: por cada cuenta pide a Aunesa `/operaciones/informes` (en paralelo), matchea cada arancel contra los boletos de `CashFlow.NegocioMovimientos` por comprobante y arma `UpdateOne` que setea `aranceles` (y el atajo `arancel` en ARS). Idempotente (solo `$set`ea), flushea en lotes de 2000 para no perder progreso, filtra futuros DLR y reintenta cuentas que fallan por timeout.

Conecta con: pega a Aunesa vía `api.services.aunesa_informes`; escribe `CashFlow.NegocioMovimientos`; persiste progreso en `Manager.AranceelesJobRuns`; lo invocan `scripts/backfill_aranceles.py` y `POST /api/manager/aunesa/boletos/backfill`.

## Usa / conecta con →
- [[api.services._negocio_arancelables]]  ·  _module_
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.aunesa_informes]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[jobs.aranceles]]  ·  _module_
