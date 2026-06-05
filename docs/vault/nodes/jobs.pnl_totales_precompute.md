---
id: jobs.pnl_totales_precompute
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\pnl_totales_precompute.py
---

# jobs/pnl_totales_precompute

> pnl_totales_precompute.py — precalcula el PnL de TODAS las cuentas.

**Archivo:** `jobs\pnl_totales_precompute.py`

## Qué hace
Job de precompute del PnL de TODAS las cuentas (~880). La vista TOTALES recorría todas las cuentas en vivo por request y se pasaba del timeout (502); este job hace ese cálculo offline y lo persiste, un documento por cuenta con sus filas y el detalle de boletos. Swap atómico (sin ventana de vacío). Corre cada 30 min (:05 y :35), después de `negocio_movimientos`.

Conecta con: invoca `api.services.pnl.pnl_todas_cuentas_compute` (motor de PnL cost-basis), escribe `Valuaciones.PnLTotalesCache` vía `reemplazar_coleccion_atomico`. Esa colección la lee el endpoint `/api/portfolio/pnl-todas`.

## Usa / conecta con →
- [[api.services.pnl]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.PnLTotalesCache]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.pnl_totales_precompute]]  ·  _cron_
