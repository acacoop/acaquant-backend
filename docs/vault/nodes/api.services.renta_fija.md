---
id: api.services.renta_fija
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/renta_fija.py
---

# api/services/renta_fija

> Capa de servicio — renta fija (MarketSnapshot + TimeSales + Curvas).

**Archivo:** `api/services/renta_fija.py`

## Qué hace
Capa de servicio de renta fija: snapshot de libro/trades, histórico de trades por ticker, serie diaria por curva, y el tool maestro `listar_curva` que enriquece cada bono con TEA/TEM/paridad/duration/convexity. Define los helpers compartidos de tickers/curvas (`resolver_ticker_exacto`, `_ticker_filter`, `_CURVAS_VALIDAS`) que el resto de services importa. Resuelve ticker corto→completo vía índice para evitar table-scans.

Conecta con: lee `Trading.MarketSnapshot`, `Trading.TimeSales` y `Trading.Curvas`. Lo invocan los routers de cotizaciones/renta fija, varias tools MCP y otros services (opciones, sensibilidad) que reusan sus helpers.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[engines.curvas]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.copiloto.renta_fija]]  ·  _module_
- [[api.services.curvas_vista]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
