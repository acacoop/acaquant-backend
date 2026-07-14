---
id: api.mcp.auth
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\mcp\auth.py
---

# api/mcp/auth

> Middleware de auth para el sub-app MCP.

**Archivo:** `api\mcp\auth.py`

## Qué hace
Middleware de autenticación del sub-app MCP. Toda request al MCP debe traer `Authorization: Bearer <token>`; acepta dos tipos: el token estático `MCP_BEARER_TOKEN` (fallback dev/curl, comparado en tiempo constante) o un JWT OAuth-issued por nuestro `/oauth/token` (path principal de Claude Desktop / claude.ai). Si no hay ni token estático ni `MCP_JWT_SECRET`, responde 503 (MCP no configurado).

Conecta con: lee `config.MCP_BEARER_TOKEN`/`MCP_JWT_SECRET`; delega la validación del JWT a `api.mcp.oauth.verify_access_token`; envuelve el sub-app FastMCP de `api.mcp.server`.

## Usa / conecta con →
- [[api.mcp.oauth]]  ·  _module_
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
