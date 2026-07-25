---
id: web.api.api.derivados-agro.estrategia.simular
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/derivados-agro/estrategia/simular/route.ts
---

# web /api/derivados-agro/estrategia/simular  (proxy)

**Archivo:** `src/app/api/derivados-agro/estrategia/simular/route.ts`

## Qué hace
POST proxy del simulador de estrategias agro: valida el body como JSON y lo reenvía al backend, que calcula el escenario. El gate de admin lo aplica el backend. Sin cache.

Conecta con: simulador de estrategias de la vista agro del front → este route → backend `POST /api/derivados/agro/estrategia/simular` (router `api/routers/derivados_agro.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
