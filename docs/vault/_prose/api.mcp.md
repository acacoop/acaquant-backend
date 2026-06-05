Paquete del servidor MCP (`api/mcp/`). Expone data 100% de mercado (curvas, forwards, breakevens, opciones, REM, etc.) como tools de solo lectura para clientes Claude (Desktop / claude.ai vía Custom Connector). No expone datos privados (portfolio, cuentas, AuM, manager). El `__init__.py` solo documenta; el setup real está en `server.py`.

Conecta con: contiene `server.py` (FastMCP), `auth.py` (bearer), `oauth.py` (OAuth 2.1) y `discovery.py`; el sub-app lo monta `api.main` en `/mcp`.
