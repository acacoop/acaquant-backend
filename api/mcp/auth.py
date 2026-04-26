"""Middleware Bearer para el sub-app MCP.

Valida `Authorization: Bearer <MCP_BEARER_TOKEN>` en todas las requests
que entran a /mcp/*. Rechaza con 401 si falta o no matchea.

El token MCP es DISTINTO del API_KEY del resto de la API:
- API_KEY: usado por acaquant-web y curl interno.
- MCP_BEARER_TOKEN: usado por Claude Desktop / Claude Code.

Si querés rotar uno sin tocar el otro, son env vars independientes.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import MCP_BEARER_TOKEN


class MCPBearerMiddleware(BaseHTTPMiddleware):
    """Bearer auth para todas las requests del sub-app MCP."""

    async def dispatch(self, request: Request, call_next):
        # Sanity: si no hay token configurado, el módulo no debería
        # haberse montado — lo chequeamos defensivamente.
        if not MCP_BEARER_TOKEN:
            return JSONResponse(
                {"error": "MCP server no configurado (MCP_BEARER_TOKEN vacío)"},
                status_code=503,
            )

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Falta header Authorization: Bearer <token>"},
                status_code=401,
            )

        token = auth[len("Bearer "):].strip()
        if token != MCP_BEARER_TOKEN:
            return JSONResponse(
                {"error": "Token MCP inválido"},
                status_code=401,
            )

        return await call_next(request)
