---
id: jobs.cierre_canje
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\cierre_canje.py
---

# jobs/cierre_canje

> cierre_canje.py — materializa el cierre diario de los tickers de canje.

**Archivo:** `jobs\cierre_canje.py`

## Qué hace
Materializa el cierre diario de los tickers de canje (pares C/D de `config.PARES_CANJE`, ej. AL30C/AL30D, GD30C/GD30D): por cada ticker toma el último trade del día y lo guarda como cierre. Así la serie del canje se arma leyendo ~1 doc por día en vez de agregar cientos de miles de ticks de TimeSales.

Corre post-cierre (17:30 ART ≈ 20:30 UTC). Idempotente: upsert por (ticker, fecha); descarta precios ≤ 0 para no persistir cierres viejos.

Conecta con: lee `Trading.TimeSales` (último trade del día por ticker), escribe `Trading.CanjeCierre`. Lo consume `api/services/canje.py::serie_canje`. Backfill inicial: `scripts/backfill_cierre_canje.py`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Trading.CanjeCierre]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.cierre_canje]]  ·  _cron_
