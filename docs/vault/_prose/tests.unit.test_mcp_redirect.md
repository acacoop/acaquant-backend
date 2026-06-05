Valida el allowlist de `redirect_uri` del Dynamic Client Registration del MCP (`api/mcp/oauth.py::_redirect_uri_permitido`), defensa anti open-redirect del OAuth. Acepta dominios y subdominios de claude.ai/claude.com, rechaza hosts ajenos, no se deja engañar por sufijos falsos (claude.ai.evil.com) y rechaza URLs inválidas o vacías.

Conecta con: importa `api.mcp.oauth`; red de seguridad del provider OAuth 2.1 del MCP server.
