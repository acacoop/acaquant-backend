"""FastMCP server para TradingAV — asistente 100% de RENTA VARIABLE.

Expone tools de SOLO LECTURA sobre datos de MERCADO de equities ARG: universo
de CEDEARs/ADRs, live del tablero, time sales intradía, retornos y stats quant
del subyacente USD, pivot points, day-trading lab y Mesa de Estrategia
(correlación / dimensionado de trades / análisis de book).

NO expone (por diseño, REGLA #8): portfolio, operaciones, cuentas, AuM, manager
ni nada de la ALyC — datos privados, no de mercado.

Las tools viven en `api/mcp/tools/` (un módulo por dominio, cada uno con un
`register(mcp)`). Este archivo solo arma el FastMCP y decide qué se registra.
Hoy: SOLO renta variable.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from api.mcp.tools import renta_variable

# Stateless HTTP: cada request se procesa independiente, sin sesión
# persistente — más simple y compatible con load balancers.
# streamable_http_path="/": que el transporte quede en el ROOT del sub-app.
# Si dejamos el default ("/mcp"), al hacer app.mount("/mcp", sub_app) la
# URL real termina en /mcp/mcp/ y no responde a /mcp/.
# transport_security: el SDK default solo acepta localhost (DNS rebinding
# protection). Custom Connector de Claude pega con Host=api.acaquant.com y
# Origin=https://claude.ai → 421 Misdirected Request si no whitelistamos.
mcp = FastMCP(
    name="TradingAV",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=["api.acaquant.com", "api.acaquant.com:443", "localhost:*", "127.0.0.1:*"],
        allowed_origins=["https://claude.ai", "https://claude.com", "http://localhost:*", "http://127.0.0.1:*"],
    ),
)

# Dominio ACTIVO: renta variable.
renta_variable.register(mcp)
