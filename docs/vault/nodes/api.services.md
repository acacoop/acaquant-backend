---
id: api.services
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/__init__.py
---

# api/services/__init__

**Archivo:** `api/services/__init__.py`

## Qué hace
Paquete `api/services` — marcador del paquete (archivo `__init__.py` vacío). Agrupa toda la capa de lógica de negocio pura de la API: services invocables tanto por los routers HTTP como por scripts, sin dependencia de FastAPI. Cada módulo hermano (portfolio, renta_fija, ordenes, pnl, etc.) resuelve un dominio.

Conecta con: lo importan los routers de `api/routers/`; los services adentro leen/escriben Mongo y pegan a fuentes externas; no contiene lógica propia.

## Lo usan (backlinks) ←
- [[api.routers.aca]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.routers.ap5]]  ·  _module_
- [[api.routers.back_office]]  ·  _module_
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.derivados_agro]]  ·  _module_
- [[api.routers.estrategia]]  ·  _module_
- [[api.routers.ia]]  ·  _module_
- [[api.routers.interbanking]]  ·  _module_
- [[api.routers.manager.aca]]  ·  _module_
- [[api.routers.manager.aca_valores]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.bonos]]  ·  _module_
- [[api.routers.manager.breakevens]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.contrapartes]]  ·  _module_
- [[api.routers.manager.control_automatico]]  ·  _module_
- [[api.routers.manager.documentos]]  ·  _module_
- [[api.routers.manager.emisores]]  ·  _module_
- [[api.routers.manager.import_tenencia]]  ·  _module_
- [[api.routers.manager.jobs]]  ·  _module_
- [[api.routers.manager.latencia]]  ·  _module_
- [[api.routers.manager.logs]]  ·  _module_
- [[api.routers.manager.mesa]]  ·  _module_
- [[api.routers.manager.ons]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.valuaciones]]  ·  _module_
- [[api.routers.market]]  ·  _module_
- [[api.routers.me]]  ·  _module_
- [[api.routers.mesa_dinero]]  ·  _module_
- [[api.routers.news]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.research1816]]  ·  _module_
- [[api.routers.research_bcra]]  ·  _module_
- [[api.routers.research_docs]]  ·  _module_
- [[api.routers.research_fred]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.routers.senebis]]  ·  _module_
- [[api.routers.trading]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[api.services.aca]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.agro_cobertura]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.bonos_admin]]  ·  _module_
- [[api.services.briefing]]  ·  _module_
- [[api.services.carteras_informe]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comercial_sql]]  ·  _module_
- [[api.services.cuantitativo_sql]]  ·  _module_
- [[api.services.diagnostico]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.mesa_dinero]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.trading_pivots]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[api.services.valuaciones_sql]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.guardrails]]  ·  _module_
- [[jobs.interbanking_sync]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
- [[jobs.saldos_a_operadores]]  ·  _module_
- [[jobs.tesoreria_snapshot]]  ·  _module_
