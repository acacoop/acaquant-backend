---
id: web.api.api.aum-diff
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\aum-diff\route.ts
---

# web /api/aum-diff  (proxy)

**Archivo:** `src\app\api\aum-diff\route.ts`

## Qué hace
Compara el AuM de dos fechas (actual vs anterior) por cuenta: arma el delta de saldo, marca cuentas nuevas/cerradas y soporta filtro por cuenta, operador y moneda (ARS/USD con MEP). Valida que vengan ambas fechas (400 si no) y proxea sin cache.

Conecta con: vista de evolución de AuM del front → este route → backend `GET /api/portfolio/diff` (service `api/services/portfolio.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
