---
id: api.db
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\db.py
---

# api/db

> Helpers de acceso a las bases Mongo (sin dependencia de FastAPI).

**Archivo:** `api\db.py`

## Qué hace
Helpers de acceso a las bases Mongo sin dependencia de FastAPI (`get_db_trading`, `get_db_valuaciones`, `get_db_cashflow`, `get_db_clientes`, `get_db_manager`, etc.). Existe separado de `api/deps.py` para que la capa de servicios (`api/services/*`) pueda importarlo sin arrastrar fastapi. Todos resuelven sobre el cliente read-only (`SECONDARY_PREFERRED`).

Conecta con: usa el singleton `core.mongo.get_mongo_client_read()`; lo consumen todos los services y varios routers; `api.deps` lo re-exporta por compatibilidad.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.deps]]  ·  _module_
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services._mep]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.day_trading]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.operaciones_view]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.rem]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_
- [[api.services.titulos_flujos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
