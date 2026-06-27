"""Tools del MCP server, agrupadas por dominio.

Cada módulo expone una función `register(mcp)` que registra sus `@mcp.tool`
sobre la instancia FastMCP que recibe. `api/mcp/server.py` decide cuáles se
registran — hoy SOLO `renta_variable`.

- `renta_variable.py` — ACTIVO. El MCP es un asistente 100% de renta variable
  (CEDEARs / ADRs / acciones / time sales / pivots / day-trading / estrategia).
- `parked_mercado.py` — PAUSADO (no se registra). Tools de renta fija, derivados,
  opciones, forwards, breakevens, cauciones, futuros DLR, MEP y macro. El código
  queda intacto; para reactivarlas, descomentar su `register(mcp)` en server.py.
"""
