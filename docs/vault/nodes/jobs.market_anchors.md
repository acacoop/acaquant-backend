---
id: jobs.market_anchors
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/market_anchors.py
---

# jobs/market_anchors

> market_anchors.py — anchors diarios de retorno (7d, MTD, YTD, 1Y).

**Archivo:** `jobs/market_anchors.py`

## Qué hace
Calcula los "anchors" de retorno (cierres de referencia a 7 días, inicio de mes, inicio de año y 1 año atrás) para cada símbolo del watchlist HOME. Fetchea ~13 meses de candle diario (Yahoo para equities/treasuries/índices, frankfurter.app para FX) y guarda el cierre más cercano a cada anchor en el mismo doc de `Market.Quotes`. Luego la API computa los retornos on-the-fly contra el last price.

Cron: 1×/día post-cierre US (22:00 UTC = 19:00 ART, L-V).

Conecta con: usa `core.yahoo.stock_candle` + frankfurter, lee las listas de símbolos de `jobs.market_quotes`, escribe campos `anchor_*` en `Market.Quotes`. Lo consume el watchlist /argy y el home.

## Usa / conecta con →
- [[core.calendario]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.yahoo]]  ·  _module_
- [[jobs.market_quotes]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.market_anchors]]  ·  _cron_
