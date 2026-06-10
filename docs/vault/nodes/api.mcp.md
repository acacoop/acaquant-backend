---
id: api.mcp
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/mcp/__init__.py
---

# api/mcp/__init__

> MCP server — expone data 100% de mercado al Claude Desktop / Claude Code.

**Archivo:** `api/mcp/__init__.py`

## Qué hace
Paquete del servidor MCP (`api/mcp/`). Expone data 100% de mercado (curvas, forwards, breakevens, opciones, REM, etc.) como tools de solo lectura para clientes Claude (Desktop / claude.ai vía Custom Connector). No expone datos privados (portfolio, cuentas, AuM, manager). El `__init__.py` solo documenta; el setup real está en `server.py`.

Conecta con: contiene `server.py` (FastMCP), `auth.py` (bearer), `oauth.py` (OAuth 2.1) y `discovery.py`; el sub-app lo monta `api.main` en `/mcp`.

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
