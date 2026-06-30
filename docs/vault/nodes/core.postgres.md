---
id: core.postgres
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/postgres.py
---

# core/postgres

> core/postgres.py — conexión a Postgres (Supabase), capa relacional analítica.

**Archivo:** `core/postgres.py`

## Qué hace
_(pendiente de enriquecimiento)_

## Lo usan (backlinks) ←
- [[api.mcp.oauth]]  ·  _module_
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.instrumentos]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.renta_variable]]  ·  _module_
- [[api.routers.manager.status]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services._idempotencia]]  ·  _module_
- [[api.services._negocio_sql_read]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.assets_sql]]  ·  _module_
- [[api.services.aunesa_aranceles]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.bonos_admin]]  ·  _module_
- [[api.services.camara_cereales]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.cashflow_sql]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comercial_sql]]  ·  _module_
- [[api.services.compliance]]  ·  _module_
- [[api.services.contrapartes_seg]]  ·  _module_
- [[api.services.control_automatico]]  ·  _module_
- [[api.services.control_comercial_sql]]  ·  _module_
- [[api.services.day_trading]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[api.services.diagnostico]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.import_tenencia]]  ·  _module_
- [[api.services.import_tenencia_sql]]  ·  _module_
- [[api.services.intraday]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.manager_infra_sql]]  ·  _module_
- [[api.services.market_sql]]  ·  _module_
- [[api.services.mercado_hist_sql]]  ·  _module_
- [[api.services.news_sql]]  ·  _module_
- [[api.services.ons]]  ·  _module_
- [[api.services.opciones_sql]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.operativa_mep_sql]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.ordenes_sql]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.portfolio_sql]]  ·  _module_
- [[api.services.rem_sql]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_
- [[api.services.segmentacion]]  ·  _module_
- [[api.services.sin_operador]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_
- [[api.services.titulos_flujos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[api.services.valuaciones_sql]]  ·  _module_
- [[core.adhoc_subscriptions]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[core.calendario]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.grupos_sql]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[core.roles]]  ·  _module_
- [[core.roles_sql]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[engines._universo_portfolio]]  ·  _module_
- [[engines.motor_agro]]  ·  _module_
- [[engines.motor_agro_opciones]]  ·  _module_
- [[engines.motor_cedears]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[engines.options]]  ·  _module_
- [[engines.valores]]  ·  _module_
- [[jobs._aum_filters]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.adr_live]]  ·  _module_
- [[jobs.archive_options_data]]  ·  _module_
- [[jobs.argentina_datos]]  ·  _module_
- [[jobs.cierre_canje]]  ·  _module_
- [[jobs.cleanup_cedears_timesales]]  ·  _module_
- [[jobs.cleanup_curvas]]  ·  _module_
- [[jobs.cleanup_futuros_dlr]]  ·  _module_
- [[jobs.consolidado_cuentas]]  ·  _module_
- [[jobs.day_trading_stats]]  ·  _module_
- [[jobs.fair_value]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.forwards_zscore]]  ·  _module_
- [[jobs.informe_salud]]  ·  _module_
- [[jobs.market_quotes]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.options_rollup]]  ·  _module_
- [[jobs.portafolio_backfill]]  ·  _module_
- [[jobs.portafolio_reparar_timeouts]]  ·  _module_
- [[jobs.precios_acciones_daily]]  ·  _module_
- [[jobs.segmentar_patrimonial]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[jobs.sync_comitentes]]  ·  _module_
- [[jobs.watchdog]]  ·  _module_
- [[quant.pivot_points]]  ·  _module_
