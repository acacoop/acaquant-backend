---
id: web.api.api.aum-pnl-todas
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\aum-pnl-todas\route.ts
---

# web /api/aum-pnl-todas  (proxy)

**Archivo:** `src\app\api\aum-pnl-todas\route.ts`

## Qué hace
Versión agregada del PnL para TODAS las cuentas (no una sola): proxea con el parámetro `filtro_cuenta` (default "todas") al backend, que lo sirve desde la cache precalculada. Sin cache de edge.

Conecta con: vista de PnL consolidado del front → este route → backend `GET /api/portfolio/pnl-todas` (alimentado por `Valuaciones.PnLTotalesCache`, job `jobs/pnl_totales_precompute.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
