---
id: jobs.backfill_forwards
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\backfill_forwards.py
---

# jobs/backfill_forwards

> Backfill histórico de Trading.ForwardsHistorico.

**Archivo:** `jobs\backfill_forwards.py`

## Qué hace
Reconstruye día a día la matriz NxN de tasas forward de una curva (cer/tasa_fija/soberanos/tamar), tomando el último trade enriquecido con TEA+duration por ticker en cada día. Usa la misma `engines.forwards.calcular_matriz` que el motor live para paridad de cálculo. Idempotente por (curva, fecha); saltea hoy por default y omite días con menos de 2 bonos.

Conecta con: lee `Trading.TimeSales` + `Trading.Curvas` (vía `engines._curvas_loader`), escribe `Trading.ForwardsHistorico`. Herramienta manual, típicamente tras un backfill de TimeSales.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[engines._curvas_loader]]  ·  _module_
- [[engines.forwards]]  ·  _module_
