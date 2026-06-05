---
id: web.api.api.derivados-agro.mejoras-dispo
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/derivados-agro/mejoras-dispo/route.ts
---

# web /api/derivados-agro/mejoras-dispo  (proxy)

**Archivo:** `src/app/api/derivados-agro/mejoras-dispo/route.ts`

## Qué hace
Proxy live de la tabla Mejoras Precio Disponible (3 bloques: Soja/Maíz/Trigo + LECAPs): pega a `/api/derivados/agro/mejoras-dispo` y devuelve sin cache para que el polling vea precios y TNAs frescos.

Conecta con: panel Mejoras Dispo de la vista agro del front → este route → backend `GET /api/derivados/agro/mejoras-dispo` (service `api/services/mejoras_dispo.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
