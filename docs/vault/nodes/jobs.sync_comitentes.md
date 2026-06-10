---
id: jobs.sync_comitentes
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/sync_comitentes.py
---

# jobs/sync_comitentes

> Sync de cuentas comitentes desde Aunesa → master `Clientes.Comitentes`.

**Archivo:** `jobs/sync_comitentes.py`

## Qué hace
Job diario que pobla/actualiza el master de clientes del Tablero Comercial desde el listado de cuentas de Aunesa. Upsert idempotente por `id_cuenta`: hace `$set` de los datos de Aunesa (denominación, estado, teléfono, etc.) PERO preserva el operador (se escribe solo al insertar, lo gestiona la mesa) y los campos de segmentación manual (nivel_1..5, cupo — solo `$setOnInsert`, nunca se pisan). Filtra tipo=Comitente + estado=Activa.

Conecta con: lee el listado de Aunesa (HTTP directo), escribe `Clientes.Comitentes` (índice único id_cuenta). Registra el run en `Manager.JobRuns`. Alimenta el Tablero Comercial y la segmentación patrimonial. Doc: `docs/TABLERO_COMERCIAL.md`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.doc_fiscal]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.sync_comitentes]]  ·  _cron_
