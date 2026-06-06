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
- [[api.mcp.server]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.compliance]]  ·  _module_
- [[api.routers.manager.control_automatico]]  ·  _module_
- [[api.routers.manager.operaciones]]  ·  _module_
- [[api.routers.manager.valuaciones]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
