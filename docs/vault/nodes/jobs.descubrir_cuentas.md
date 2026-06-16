---
id: jobs.descubrir_cuentas
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\descubrir_cuentas.py
---

# jobs/descubrir_cuentas

> Descubre cuentas autorizadas para el user master del broker.

**Archivo:** `jobs\descubrir_cuentas.py`

## Qué hace
Descubre qué cuentas autoriza el broker para el user master: barre un rango de IDs (default 1-12000) llamando a pyRofex `get_account_report` por cada una y guarda las autorizadas con su snapshot de saldos (ARS/USD disponible, n° posiciones, flag activa). Las no autorizadas se ignoran en silencio.

Pensado como backfill (1ra corrida ~40 min) y luego 1×/día para detectar cuentas nuevas. 100% read-only contra el broker; idempotente (upsert por account_id); con timeout de socket y cota de tiempo total para no colgarse.

Conecta con: pyRofex (broker, sesión LIVE), escribe `Operaciones.AccountsDescubiertas`. Creds desde `.env` (ROFEX_USER/PASSWORD/ACCOUNT).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.descubrir_cuentas]]  ·  _cron_
