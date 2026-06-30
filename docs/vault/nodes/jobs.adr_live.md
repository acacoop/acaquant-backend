---
id: jobs.adr_live
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/adr_live.py
---

# jobs/adr_live

> adr_live.py — pull live USD prices del subyacente de cada CEDEAR → SQL.

**Archivo:** `jobs/adr_live.py`

## Qué hace
Trae el precio USD live del subyacente (NYSE/Nasdaq) de cada CEDEAR activo y lo upsertea, un doc por ticker. Corre cada 15 min en horario de mercado USA. El scanner de CEDEARs combina este precio live con los cierres EOD para mostrar precio actual + retornos rolling (7d/MTD/YTD).

Conecta con: lee `Trading.Cedears` (underlyings activos), pega a Finnhub vía `core.finnhub.quote`, escribe `Trading.AdrSnapshot`; consumido por `api.services.scanner`. Cron en `deploy/crontab.txt` (`*/15 13-20 L-V`).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.finnhub]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.adr_live]]  ·  _cron_
