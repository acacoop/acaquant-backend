---
id: api.mcp.oauth
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/mcp/oauth.py
---

# api/mcp/oauth

> OAuth 2.1 provider para el MCP server — login delegado a Cloudflare Access.

**Archivo:** `api/mcp/oauth.py`

## Qué hace
Provider OAuth 2.1 + PKCE del servidor MCP, con el login delegado a Cloudflare Access. Implementa el flujo: registro dinámico de cliente (`/oauth/register`, DCR), `/oauth/authorize` (CF Access desafía al user, leemos su email del JWT y emitimos un authorization code) y `/oauth/token` (intercambia el code por un access_token JWT firmado por nosotros). También valida tokens (`verify_access_token`).

Conecta con: persiste clients/codes/tokens en la DB Mongo `MCP` (TTL automático en codes 10min, tokens 1h); usa `api.auth.get_user_email` para sacar la identidad de CF; emite los tokens que valida `api.mcp.auth`.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[config]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[api.mcp.auth]]  ·  _module_
