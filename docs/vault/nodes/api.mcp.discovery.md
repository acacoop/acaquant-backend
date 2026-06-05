---
id: api.mcp.discovery
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/mcp/discovery.py
---

# api/mcp/discovery

> Discovery endpoints OAuth: lo que Claude Desktop busca primero.

**Archivo:** `api/mcp/discovery.py`

## Qué hace
Endpoints de descubrimiento OAuth (públicos, sin auth) que el cliente Claude consulta antes de autenticarse. Sirve `/.well-known/oauth-protected-resource` (RFC 9728: "este recurso lo protege este authorization server") y `/.well-known/oauth-authorization-server` (RFC 8414: ubicación de authorize/token/register y métodos PKCE). Incluye variantes con sufijo de path que buscan algunos clientes.

Conecta con: lee `config.MCP_OAUTH_ISSUER`; apunta al provider OAuth de `api.mcp.oauth`; estos paths deben estar exentos en Cloudflare Access (bypass) o el connector muere.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
