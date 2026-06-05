---
id: web.api.api.futuros-dlr
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/futuros-dlr/route.ts
---

# web /api/futuros-dlr  (proxy)

**Archivo:** `src/app/api/futuros-dlr/route.ts`

## Qué hace
Proxy live de los futuros DLR (Dólar A3500): pega a `/api/cotizaciones/futuros-dlr` y devuelve sin cache, porque el edge cache pisaba el polling del cliente.

Conecta con: vista de futuros DLR del front → este route → backend `GET /api/cotizaciones/futuros-dlr` (motor `engines/futuros_dlr.py`, colección `Trading.FuturosDLRSnapshot`).

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
