---
id: api.mcp.server
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/mcp/server.py
---

# api/mcp/server

> FastMCP server para TradingAV.

**Archivo:** `api/mcp/server.py`

## Qué hace
Servidor FastMCP de TradingAV: define las tools de solo lectura sobre datos de mercado (curvas, forwards, breakevens, cauciones, futuros DLR, opciones, REM, descomposición de retorno, sensibilidad, canje, carry trade, MEP, macro, order book, scanner). Cada tool es un thin wrapper sobre un service puro de `api/services/*`. Configurado stateless-http y con `TransportSecuritySettings` que whitelistea `api.acaquant.com` + origins de claude.ai/claude.com (si no, el connector recibe 421).

Conecta con: importa decenas de `api.services.*`; el sub-app lo monta `api.main` en `/mcp` detrás del middleware `api.mcp.auth`.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.rem]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
