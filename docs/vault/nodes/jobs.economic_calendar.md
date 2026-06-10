---
id: jobs.economic_calendar
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/economic_calendar.py
---

# jobs/economic_calendar

> economic_calendar.py — ingesta diaria del calendario económico global.

**Archivo:** `jobs/economic_calendar.py`

## Qué hace
Ingesta diaria del calendario económico global (FOMC, CPI, jobs, etc.) desde Finnhub, que devuelve ~60 días forward en un solo endpoint. Normaliza el "impact" a int (0-3) y hace upsert idempotente por (time, country, event).

Cron: 1×/día a las 06:00 UTC (L-V).

Conecta con: usa `core.finnhub.economic_calendar`, escribe `Market.EconomicCalendar`. Lo consume el router de market (calendario económico) en la API/home.

## Usa / conecta con →
- [[core.finnhub]]  ·  _module_
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.economic_calendar]]  ·  _cron_
