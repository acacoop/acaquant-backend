---
id: api.services.pnl
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\pnl.py
---

# api/services/pnl

> Motor de PnL por (cuenta, ticker) con cost-basis weighted-average.

**Archivo:** `api\services\pnl.py`

## Qué hace
Motor de PnL por (cuenta, ticker) con cost-basis weighted-average. Reconstruye los boletos compra/venta en orden cronológico y separa PnL realizado, PnL no realizado (stock vivo a precio actual), PnL pasivo (cupones/dividendos/amortizaciones) y total. Pesifica cada importe USD al MEP de su fecha. Marca completeness "parcial" cuando había posición previa al primer boleto disponible.

Conecta con: lee boletos de `CashFlow`, precios de `Trading` y AuM/precio actual de `Valuaciones`; usa `api.services._mep` para pesificar. Lo consumen las vistas de valuaciones/PnL y el precompute `jobs.pnl_totales_precompute`.

## Usa / conecta con →
- [[api.services._mep]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.services.pnl_sql]]  ·  _module_
