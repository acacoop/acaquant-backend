---
id: api.cache
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/cache.py
---

# api/cache

> Cache in-process para endpoints FastAPI.

**Archivo:** `api/cache.py`

## Qué hace
Cache in-process para handlers de FastAPI vía decorador `@cached(ttl=N)`. Como la API es un único proceso, alcanza un dict con TTL (sin Redis): ahorra un round-trip a Mongo por request repetido dentro de la ventana. La llave es nombre de función + kwargs; el store está acotado (sweep de expiradas + eviction LRU al pasar 512 entradas) para evitar el leak de RAM que tuvo en producción. No cachea respuestas vacías (negative caching off).

Conecta con: lo importan los routers (`cuentas`, `carteras`, etc.) para envolver endpoints; complementa el caching de la capa de servicios.

## Lo usan (backlinks) ←
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.logs]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.research1816]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services.aca]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.av_agent_evals]]  ·  _module_
- [[api.services.av_agent_vista]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.breakevens_admin]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.cashflow_sql]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.control_comercial_sql]]  ·  _module_
- [[api.services.curvas_vista]]  ·  _module_
- [[api.services.day_trading]]  ·  _module_
- [[api.services.db_obs]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.diagnostico]]  ·  _module_
- [[api.services.estrategia]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.financiamiento]]  ·  _module_
- [[api.services.jobs_catalogo]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.market_sql]]  ·  _module_
- [[api.services.mercado_hist_sql]]  ·  _module_
- [[api.services.mesa_dinero]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.opciones_sql]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.rem_sql]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.research_1816_sql]]  ·  _module_
- [[api.services.research_bcra_sql]]  ·  _module_
- [[api.services.research_fred_sql]]  ·  _module_
- [[api.services.research_sql]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.salud]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.sin_operador]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_
- [[api.services.tesoreria]]  ·  _module_
- [[api.services.titulos_negativos]]  ·  _module_
- [[api.services.trading_pivots]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
