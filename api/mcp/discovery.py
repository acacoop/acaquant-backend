"""Discovery endpoints OAuth: lo que Claude Desktop busca primero.

- /.well-known/oauth-protected-resource (RFC 9728): le dice al cliente
  "este recurso está protegido por este authorization server".
- /.well-known/oauth-authorization-server (RFC 8414): metadata del AS:
  endpoints (authorize, token, register), métodos de PKCE soportados, etc.

Ambos son públicos (no requieren auth). Claude Desktop los hits sin token
ANTES del flujo OAuth para descubrir cómo autenticarse.
"""
from __future__ import annotations

from fastapi import APIRouter

from config import MCP_OAUTH_ISSUER

router = APIRouter(tags=["MCP Discovery"])


@router.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource_metadata():
    """RFC 9728. Claude Desktop hits esto cuando ve el MCP server por
    primera vez para descubrir el authorization server."""
    return {
        "resource": f"{MCP_OAUTH_ISSUER}/mcp",
        "authorization_servers": [MCP_OAUTH_ISSUER],
        "scopes_supported": ["mcp:read"],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
def oauth_authorization_server_metadata():
    """RFC 8414. Metadata del Authorization Server: dónde están los endpoints,
    qué grant types se soportan, qué métodos de PKCE."""
    return {
        "issuer": MCP_OAUTH_ISSUER,
        "authorization_endpoint": f"{MCP_OAUTH_ISSUER}/oauth/authorize",
        "token_endpoint": f"{MCP_OAUTH_ISSUER}/oauth/token",
        "registration_endpoint": f"{MCP_OAUTH_ISSUER}/oauth/register",
        "scopes_supported": ["mcp:read"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
