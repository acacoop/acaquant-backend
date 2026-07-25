---
id: web.api.api.derivados-agro
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/derivados-agro/route.ts
---

# web /api/derivados-agro  (proxy)

**Archivo:** `src/app/api/derivados-agro/route.ts`

## Qué hace
Proxy live de la tabla PASE AGRO (Trigo/Maíz/Soja Rosario): pega a `/api/derivados/agro` y devuelve sin cache para que el polling vea cambios en tiempo real (last_price, oficial, pizarra editada).

Conecta con: vista Pase Agro del front → este route → backend `GET /api/derivados/agro` (service `api/services/derivados_agro.py`, motor `engines/motor_agro.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
