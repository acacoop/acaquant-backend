---
id: web.api.api.aum-pnl
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/aum-pnl/route.ts
---

# web /api/aum-pnl  (proxy)

**Archivo:** `src/app/api/aum-pnl/route.ts`

## Qué hace
Devuelve el PnL por ticker de UNA cuenta (cost-basis weighted-average): cantidad/valor actual, cash pagado y cobrado por venta y por pasivo, PnL total y %, más flags de completitud y moneda mixta. Requiere `id_cuenta` (400 si falta). Sin cache.

Conecta con: vista de PnL por cuenta del front → este route → backend `GET /api/portfolio/pnl` (motor de PnL `api/services/pnl.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
