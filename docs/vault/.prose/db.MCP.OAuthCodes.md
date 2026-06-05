Códigos de autorización OAuth 2.1 (efímeros, con TTL automático) del MCP server, en la base `MCP`. Almacena el authorization code intermedio del flow PKCE entre `/oauth/authorize` y `/oauth/token`.

Conecta con: la escribe y consume `api/mcp/oauth.py` (provider OAuth del MCP). Expira sola por índice TTL de Mongo.
