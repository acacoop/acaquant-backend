---
id: tests.unit.test_mcp_redirect
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_mcp_redirect.py
---

# tests/unit/test_mcp_redirect

> Test del allowlist de redirect_uri del DCR del MCP (anti open-redirect).

**Archivo:** `tests/unit/test_mcp_redirect.py`

## Qué hace
Valida el allowlist de `redirect_uri` del Dynamic Client Registration del MCP (`api/mcp/oauth.py::_redirect_uri_permitido`), defensa anti open-redirect del OAuth. Acepta dominios y subdominios de claude.ai/claude.com, rechaza hosts ajenos, no se deja engañar por sufijos falsos (claude.ai.evil.com) y rechaza URLs inválidas o vacías.

Conecta con: importa `api.mcp.oauth`; red de seguridad del provider OAuth 2.1 del MCP server.

## Usa / conecta con →
- [[api.mcp.oauth]]  ·  _module_
