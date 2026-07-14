---
id: web.api.api.aum-total.snapshot
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\aum-total\snapshot\route.ts
---

# web /api/aum-total/snapshot  (proxy)

**Archivo:** `src\app\api\aum-total\snapshot\route.ts`

## Qué hace
Devuelve la foto del AuM total de una fecha puntual: una fila por unidad (cartera, tipo, cuenta, valuación, cantidad) más el MEP usado y flag de MEP faltante. Requiere `fecha` (400 si falta); admite filtro por cuenta, operador y moneda. Sin cache.

Conecta con: detalle por día de la vista AuM total del front → este route → backend `GET /api/portfolio/total-snapshot` (service `api/services/portfolio.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
