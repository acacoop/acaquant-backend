---
id: core
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\__init__.py
---

# core/__init__

**Archivo:** `core\__init__.py`

## Qué hace
Paquete `core/` — capa de infraestructura del backend. Agrupa los clientes externos (Aunesa, BYMA, Finnhub, Yahoo, argentinadatos, MAE, Atlas), el acceso a Mongo y los helpers transversales (roles, grupos, job_runs, snapshot_writer, websocket). Su `__init__.py` está vacío: solo marca el paquete.

Regla dura: `core/` no importa nada del resto del proyecto salvo `config` — es la base sobre la que se apoyan `engines/`, `jobs/` y `api/services/`.

Conecta con: lo importan engines, jobs y api/services; no depende de ellos.

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.options]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.services._mep]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.asistente]]  ·  _module_
- [[api.services.asistente_comercial]]  ·  _module_
- [[api.services.asistente_tools]]  ·  _module_
- [[api.services.aunesa_informes]]  ·  _module_
- [[api.services.bonos_admin]]  ·  _module_
- [[api.services.camara_cereales]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.compliance]]  ·  _module_
- [[api.services.contrapartes_seg]]  ·  _module_
- [[api.services.copiloto.motor]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.ia_obs]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.ons]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.operativa_mep]]  ·  _module_
- [[api.services.ordenes]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_
- [[api.services.tesoreria]]  ·  _module_
- [[api.services.titulos_flujos]]  ·  _module_
- [[api.services.trading_pivots]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[core.ai]]  ·  _module_
- [[core.brackets]]  ·  _module_
- [[core.grupos]]  ·  _module_
- [[core.roles]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_
- [[engines.caucion]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[engines.futuros_dlr]]  ·  _module_
- [[engines.motor_agro]]  ·  _module_
- [[engines.motor_agro_opciones]]  ·  _module_
- [[engines.motor_cedears]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
- [[engines.options]]  ·  _module_
- [[engines.valores]]  ·  _module_
- [[jobs.adr_live]]  ·  _module_
- [[jobs.backfill_tasas]]  ·  _module_
- [[jobs.bcra_research]]  ·  _module_
- [[jobs.bonos_ohlc_daily]]  ·  _module_
- [[jobs.cedears_ohlc_daily]]  ·  _module_
- [[jobs.cleanup_curvas]]  ·  _module_
- [[jobs.day_trading_stats]]  ·  _module_
- [[jobs.fred_research]]  ·  _module_
- [[jobs.guardrails]]  ·  _module_
- [[jobs.mercado_1816_discovery]]  ·  _module_
- [[jobs.mercado_1816_series]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.precios_acciones_daily]]  ·  _module_
- [[jobs.research_mail]]  ·  _module_
- [[jobs.snapshot_cierre]]  ·  _module_
- [[jobs.triage]]  ·  _module_
- [[jobs.volatilidad_ggal]]  ·  _module_
