---
id: api.services.valuaciones
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/valuaciones.py
---

# api/services/valuaciones

> Valuaciones — performance e historia por cuenta.

**Archivo:** `api/services/valuaciones.py`

## Qué hace
Service de performance e historia por cuenta, con dos enfoques convivientes: (A) AUM-BASED para /serie y /mensual, que suma el snapshot MTM diario y lo combina con flujos externos (depósitos/extracciones) para la mensualización "valor de cierre + flujo neto" y la TIR; (B) COST-BASIS LEDGER para /posiciones, que reconstruye lots de boletos para PnL realizado vs no realizado por ticker. Pesifica monedas USD al MEP del día.

Conecta con: lee `Valuaciones.AuM`, `CashFlow.NegocioMovimientos` (flujos) y `Trading`; usa `quant.xirr` para la TIR. Lo invoca el router `/api/valuaciones`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[db.Valuaciones.ConsolidadoCuentas]]  ·  _collection_
- [[db.Valuaciones.Dolar]]  ·  _collection_
- [[quant.xirr]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.valuaciones]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[jobs.consolidado_cuentas]]  ·  _module_
