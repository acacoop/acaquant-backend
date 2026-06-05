---
id: web.api.api.derivados-agro.pizarra.[commodity]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\derivados-agro\pizarra\[commodity]\route.ts
---

# web /api/derivados-agro/pizarra/[commodity]  (proxy)

**Archivo:** `src\app\api\derivados-agro\pizarra\[commodity]\route.ts`

## Qué hace
PATCH proxy de la pizarra (precios manuales) de un commodity agro: valida el body como JSON y lo reenvía al backend. El gate trader+admin lo aplica el backend.

Conecta con: edición de pizarra de la vista agro del front → este route → backend `PATCH /api/derivados/agro/pizarra/{commodity}` (router `api/routers/derivados_agro.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
