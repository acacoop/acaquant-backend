---
id: api.services.repo
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\repo.py
---

# api/services/repo

> Capa de servicio — mercado repo (caución).

**Archivo:** `api\services\repo.py`

## Qué hace
Capa de servicio del mercado repo (caución): la caución es una operación a plazo sobre efectivo garantizada por títulos, funcionalmente un repo market. Expone el snapshot live por moneda (TNA last/bid/offer/open/high/low/cierre + plazo) y la serie histórica de cierres diarios. Funciones cacheadas (5s live, 5min histórico).

Conecta con: lee `Trading.CaucionSnapshot` (live) y `Trading.Caucion` (cierre), ambas escritas por `engines.caucion`. Lo invoca el router de cotizaciones/repo y la tool MCP de caución.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.server]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
