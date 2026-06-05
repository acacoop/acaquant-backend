---
id: api.services.risk
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/risk.py
---

# api/services/risk

> Servicio RISK — datos de cuenta del broker (saldos, posiciones, márgenes).

**Archivo:** `api/services/risk.py`

## Qué hace
Servicio RISK: datos de cuenta del broker (saldos, posiciones, márgenes) vía los endpoints REST `rest/risk/...` de pyRofex. Funciones puras con cache 3-5s para no machacar al broker bajo polling de la UI. Parsea sub-bloques por rueda (CI/24hs) y distingue los múltiples tipos de USD del broker (expone MEP y ARS).

Conecta con: usa la sesión pyRofex compartida de `core.rofex_orders_session` (misma que `ordenes`); no toca Mongo. Lo invoca el router `/api/risk`, gateado por módulo `operaciones` (admin+trader, no sales).

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.rofex_orders_session]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.CuentasAPI.AccionistasAPI]]  ·  _collection_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.risk]]  ·  _module_
