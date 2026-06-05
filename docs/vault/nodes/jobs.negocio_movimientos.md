---
id: jobs.negocio_movimientos
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\negocio_movimientos.py
---

# jobs/negocio_movimientos

> negocio_movimientos.py — pega a Aunesa, consolida y persiste boletos

**Archivo:** `jobs\negocio_movimientos.py`

## Qué hace
Pega a Aunesa (consolidados generales), parsea y categoriza las líneas raw de cada boleto del día, las agrupa por comprobante y persiste cada boleto consolidado en `CashFlow.NegocioMovimientos` (con cuenta, id_cuenta, categoría, ticker, importe, moneda, plazo, etc.). Denormaliza `id_cuenta` (extraído de "[805] NOMBRE") para que las queries comerciales usen índice en vez de regex.

Cron: una corrida por hora de 12 a 22 ART (15-22 UTC) L-V. Idempotente por (fecha, comprobante).

Conecta con: usa `api.services.aunesa_negocio` (pega a Aunesa) y `api.services._mep` (MEP por fecha), escribe `CashFlow.NegocioMovimientos`. Es la fuente que alimenta la vista /operaciones/negocio, el Tablero Comercial y encadena `jobs.fci_bilateral`.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services._mep]]  ·  _module_
- [[api.services.aunesa_negocio]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.negocio_movimientos]]  ·  _cron_
