---
id: engines.dolar_mep
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\dolar_mep.py
---

# engines/dolar_mep

**Archivo:** `engines\dolar_mep.py`

## Qué hace
Job puntual (no es always-on) que calcula el dólar MEP, CCL y canje a partir de las puntas de AL30/AL30D/AL30C vía REST de pyRofex (offer de AL30 sobre bid de AL30D = MEP). Persiste un doc por corrida como serie histórica; el CCL es best-effort (si AL30C no tiene bid, no rompe).

Conecta con: escribe el histórico de MEP/CCL/canje en `Valuaciones.Dolar`; lee precios live vía `core.rofex_session` (pyRofex REST). Corre por cron cada ~15 min (`cron.engines.dolar_mep`). El live intradiario lo cubre `engines.dolares`; el endpoint `/api/cotizaciones/mep` usa este histórico como fallback.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.rofex_session]]  ·  _module_
- [[db.Valuaciones.Dolar]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.engines.dolar_mep]]  ·  _cron_
