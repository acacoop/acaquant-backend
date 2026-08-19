---
id: api
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/__init__.py
---

# api/__init__

**Archivo:** `api/__init__.py`

## Qué hace
Paquete raíz de la API FastAPI (`api/`). Es solo el marcador de paquete (`__init__.py` vacío); el código vive en `api/main.py` (entrypoint), `api/routers/` (HTTP), `api/services/` (lógica pura) y `api/mcp/` (servidor MCP).

Conecta con: agrupa todos los submódulos de la API; lo arranca uvicorn vía `api.main:app`.

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[api.services.av_agent_seguridad]]  ·  _module_
