"""Middleware de auth para el sub-app MCP.

Acepta DOS tipos de bearer en `Authorization: Bearer <token>`:

1. JWT OAuth-issued (path principal): emitido por nuestro /oauth/token
   después que el user se autenticó vía CF Access. Validamos firma,
   expiry, audience y que el jti no haya sido revocado.
   Usado por Claude Desktop / claude.ai / Claude Code via Custom Connector.

2. Static bearer (fallback dev/curl): MCP_BEARER_TOKEN del .env.
   Sigue funcionando para scripts y debugging interno.

Si NINGUNO de los dos está configurado, el sub-app MCP no se monta
(ver api/main.py).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import MCP_BEARER_TOKEN, MCP_JWT_SECRET


class MCPBearerMiddleware(BaseHTTPMiddleware):
    """Bearer auth para todas las requests del sub-app MCP.

    Match en orden: static token (constant-time compare) → JWT OAuth-issued.
    """

    async def dispatch(self, request: Request, call_next):
        if not (MCP_BEARER_TOKEN or MCP_JWT_SECRET):
            return JSONResponse(
                {"error": "MCP no configurado (sin token estático ni JWT secret)"},
                status_code=503,
            )

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Falta header Authorization: Bearer <token>"},
                status_code=401,
            )
        token = auth[len("Bearer "):].strip()

        # 1. Static bearer (fallback dev/curl).
        if MCP_BEARER_TOKEN and token == MCP_BEARER_TOKEN:
            return await call_next(request)

        # 2. JWT OAuth-issued (path principal Claude clients).
        if MCP_JWT_SECRET:
            from api.mcp.oauth import verify_access_token  # lazy: evita ciclo
            claims = verify_access_token(token)
            if claims:
                # Propagamos identidad al request scope por si alguna tool
                # quiere saber quién está llamando.
                request.scope["mcp_user"] = claims.get("sub")
                return await call_next(request)

        return JSONResponse({"error": "Token MCP inválido"}, status_code=401)
