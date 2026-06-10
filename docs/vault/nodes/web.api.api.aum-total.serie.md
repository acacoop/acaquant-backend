---
id: web.api.api.aum-total.serie
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/aum-total/serie/route.ts
---

# web /api/aum-total/serie  (proxy)

**Archivo:** `src/app/api/aum-total/serie/route.ts`

## Qué hace
Devuelve la serie temporal del AuM total: total por fecha con desglose por cartera y el MEP usado, en un rango `desde`/`hasta`, con filtro por cuenta, operador y moneda (ARS/USD). Reporta fechas sin MEP. Sin cache.

Conecta con: gráfico de evolución de AuM total del front → este route → backend `GET /api/portfolio/total-serie` (service `api/services/portfolio.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
