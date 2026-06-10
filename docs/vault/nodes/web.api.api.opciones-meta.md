---
id: web.api.api.opciones-meta
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/opciones-meta/route.ts
---

# web /api/opciones-meta  (proxy)

**Archivo:** `src/app/api/opciones-meta/route.ts`

## Qué hace
Route handler de la meta de opciones GGAL: GET trae VR/tasa (/api/cotizaciones/opciones/meta) y PUT actualiza la tasa (/api/cotizaciones/opciones/tasa?valor=). Sin cache para que el header de Derivados refresque al toque.
- Conecta con: backend /api/cotizaciones/opciones/* (api.routers.cotizaciones, motor engines.options); usado por el header del módulo Derivados.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
