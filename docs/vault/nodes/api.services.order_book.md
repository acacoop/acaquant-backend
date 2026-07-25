---
id: api.services.order_book
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/order_book.py
---

# api/services/order_book

> Capa de servicio — Order Book (LOB) live.

**Archivo:** `api/services/order_book.py`

## Qué hace
Devuelve el libro de órdenes (LOB) live de un ticker, con profundidad 5 (bids/offers) más precios open/high/low/last/cierre. Sin histórico: solo el último estado vivo. Acepta ticker completo o corto+plazo y cubre todos los tickers que el motor suscribe, no solo los de curva. Latencia ~10-50ms.

Conecta con: lee `Trading.MarketSnapshot` (lo popula `engines/valores.py` cada 1s desde pyRofex WS) con proyección acotada al libro; cachea la lista de tickers por curva desde `Trading.Curvas`. Lo invoca el router de cotizaciones / order book.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.services.copiloto.trading]]  ·  _module_
