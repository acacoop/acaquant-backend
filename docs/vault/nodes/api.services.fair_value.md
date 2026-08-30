---
id: api.services.fair_value
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/fair_value.py
---

# api/services/fair_value

> fair_value.py — service del módulo Fair Value relativo intra-curva.

**Archivo:** `api/services/fair_value.py`

## Qué hace
Service del módulo Fair Value relativo intra-curva. Tres entrypoints: `get_fair_value_live` (intra-rueda — usa la β del último cierre y los TEAs vivos para recomputar tea_teorica, residuo_bps y z_estatico), `get_fair_value_cierre` (lee el cierre persistido de una fecha) y `get_fair_value_historico_bono` (serie de residuos diarios de un bono para el drill-down). Patrón split-persist: el agregado lento vive en Mongo, el TEA live se mezcla en el service para evitar lag.

Conecta con: lee `Trading.FitParams` (β del cierre), `Trading.FairValueResiduos` (residuos persistidos por `jobs.fair_value`) y `MarketSnapshot.metrics.TEA` (live); lo consume el endpoint de fair value.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services._sql]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.cotizaciones]]  ·  _module_
