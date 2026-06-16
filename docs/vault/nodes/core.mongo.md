---
id: core.mongo
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\mongo.py
---

# core/mongo

**Archivo:** `core\mongo.py`

## Qué hace
El conector central a MongoDB Atlas. Expone dos singletons thread-safe: `get_mongo_client()` (read-write, pool 20, para motores y crons) y `get_mongo_client_read()` (read-only, `SECONDARY_PREFERRED`, pool 50, para la API). Lee `MONGO_URI`/`MONGO_URI_READ` del `.env`, comparte un único connection pool por proceso y deja que el driver reconecte solo (sin ping por llamada, que costaba ~180ms). Incluye `reemplazar_coleccion_atomico()` para reescribir colecciones precompute enteras sin ventana de vacío (swap por rename atómico).

Conecta con: TODO el backend (`engines/`, `jobs/`, `api/services/`) pasa por acá para hablar con Atlas. Registra el `core.mongo_monitor` antes de crear el cliente. Nunca cerrar estos singletons — mata el pool.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.mongo_monitor]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.db]]  ·  _module_
- [[api.main]]  ·  _module_
- [[api.mcp.oauth]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.instrumentos]]  ·  _module_
- [[api.routers.manager.jobs]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.options]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.market]]  ·  _module_
- [[api.routers.news]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services.camara_cereales]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[api.services.diagnostico]]  ·  _module_
- [[api.services.import_tenencia]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.ons]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[core.adhoc_subscriptions]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.grupos]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.openfigi]]  ·  _module_
- [[core.roles]]  ·  _module_
- [[core.snapshot_writer]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_
- [[engines.breakevens]]  ·  _module_
- [[engines.caucion]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.dolar_mep]]  ·  _module_
- [[engines.dolares]]  ·  _module_
- [[engines.forwards]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
- [[engines.motor_agro]]  ·  _module_
- [[engines.motor_agro_opciones]]  ·  _module_
- [[engines.motor_cedears]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[engines.options]]  ·  _module_
- [[engines.portfolio_snapshot]]  ·  _module_
- [[engines.valores]]  ·  _module_
- [[jobs.acreencias]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.adr_live]]  ·  _module_
- [[jobs.aranceles]]  ·  _module_
- [[jobs.archive_options_data]]  ·  _module_
- [[jobs.argentina_datos]]  ·  _module_
- [[jobs.backfill_breakevens]]  ·  _module_
- [[jobs.backfill_forwards]]  ·  _module_
- [[jobs.bcra]]  ·  _module_
- [[jobs.cashflow]]  ·  _module_
- [[jobs.cierre_canje]]  ·  _module_
- [[jobs.cleanup_cedears_timesales]]  ·  _module_
- [[jobs.cleanup_curvas]]  ·  _module_
- [[jobs.cleanup_futuros_dlr]]  ·  _module_
- [[jobs.consolidado_cuentas]]  ·  _module_
- [[jobs.day_trading_stats]]  ·  _module_
- [[jobs.descubrir_cuentas]]  ·  _module_
- [[jobs.dias_habiles]]  ·  _module_
- [[jobs.economic_calendar]]  ·  _module_
- [[jobs.fair_value]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.flujo_contrapartes]]  ·  _module_
- [[jobs.forwards_zscore]]  ·  _module_
- [[jobs.informe_salud]]  ·  _module_
- [[jobs.market_anchors]]  ·  _module_
- [[jobs.market_quotes]]  ·  _module_
- [[jobs.news_finnhub]]  ·  _module_
- [[jobs.news_ingesta]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.options_rollup]]  ·  _module_
- [[jobs.partner_export]]  ·  _module_
- [[jobs.pnl_totales_precompute]]  ·  _module_
- [[jobs.precios_acciones_daily]]  ·  _module_
- [[jobs.segmentar_patrimonial]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[jobs.snapshot_sinteticos]]  ·  _module_
- [[jobs.sync_postgres]]  ·  _module_
- [[jobs.tenencia_hd]]  ·  _module_
- [[jobs.volatilidad_ggal]]  ·  _module_
- [[jobs.watchdog]]  ·  _module_
- [[quant.black_scholes]]  ·  _module_
- [[quant.pivot_points]]  ·  _module_
