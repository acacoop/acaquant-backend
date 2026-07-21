---
id: api.services
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\__init__.py
---

# api/services/__init__

**Archivo:** `api\services\__init__.py`

## Qué hace
Paquete `api/services` — marcador del paquete (archivo `__init__.py` vacío). Agrupa toda la capa de lógica de negocio pura de la API: services invocables tanto por los routers HTTP como por scripts, sin dependencia de FastAPI. Cada módulo hermano (portfolio, renta_fija, ordenes, pnl, etc.) resuelve un dominio.

Conecta con: lo importan los routers de `api/routers/`; los services adentro leen/escriben Mongo y pegan a fuentes externas; no contiene lógica propia.

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.mcp.tools.renta_variable]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.routers.asistente]]  ·  _module_
- [[api.routers.back_office]]  ·  _module_
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.derivados_agro]]  ·  _module_
- [[api.routers.ia]]  ·  _module_
- [[api.routers.manager.aca_valores]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.bonos]]  ·  _module_
- [[api.routers.manager.breakevens]]  ·  _module_
- [[api.routers.manager.compliance]]  ·  _module_
- [[api.routers.manager.contrapartes]]  ·  _module_
- [[api.routers.manager.control_automatico]]  ·  _module_
- [[api.routers.manager.documentos]]  ·  _module_
- [[api.routers.manager.import_tenencia]]  ·  _module_
- [[api.routers.manager.jobs]]  ·  _module_
- [[api.routers.manager.ons]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.roles]]  ·  _module_
- [[api.routers.manager.uso]]  ·  _module_
- [[api.routers.manager.valuaciones]]  ·  _module_
- [[api.routers.market]]  ·  _module_
- [[api.routers.news]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.research]]  ·  _module_
- [[api.routers.research1816]]  ·  _module_
- [[api.routers.research_bcra]]  ·  _module_
- [[api.routers.research_docs]]  ·  _module_
- [[api.routers.research_fred]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.routers.trading]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.agro_cobertura]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.asistente]]  ·  _module_
- [[api.services.bonos_admin]]  ·  _module_
- [[api.services.briefing]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.copiloto.agro]]  ·  _module_
- [[api.services.copiloto.ayuda]]  ·  _module_
- [[api.services.copiloto.home]]  ·  _module_
- [[api.services.copiloto.ons]]  ·  _module_
- [[api.services.copiloto.opciones]]  ·  _module_
- [[api.services.copiloto.renta_fija]]  ·  _module_
- [[api.services.copiloto.renta_variable]]  ·  _module_
- [[api.services.copiloto.research]]  ·  _module_
- [[api.services.copiloto.trading]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.diagnostico]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.operaciones_sql]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.trading_pivots]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
