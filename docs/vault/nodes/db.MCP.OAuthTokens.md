---
id: db.MCP.OAuthTokens
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# MCP.OAuthTokens

> Colección Mongo en DB MCP.

## Qué hace
Tokens OAuth emitidos (access/refresh, con TTL automático) del MCP server, en la base `MCP`. Persiste los tokens que validan a Claude Desktop / claude.ai como Custom Connector contra `/mcp`.

Conecta con: la escribe y valida `api/mcp/oauth.py`; los JWTs se firman con `MCP_JWT_SECRET`. Expira sola por índice TTL de Mongo.

## Lo usan (backlinks) ←
- [[api.mcp.oauth]]  ·  _module_
