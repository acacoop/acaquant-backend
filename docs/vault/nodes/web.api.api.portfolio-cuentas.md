---
id: web.api.api.portfolio-cuentas
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/portfolio-cuentas/route.ts
---

# web /api/portfolio-cuentas  (proxy)

**Archivo:** `src/app/api/portfolio-cuentas/route.ts`

## Qué hace
Route handler que trae la lista de cuentas (id_cuenta + nombre) para los selectores de la vista AuM/Portfolio. Pega a /api/portfolio/cuentas y envuelve la respuesta en {cuentas}. force-dynamic + no-store.
- Conecta con: backend /api/portfolio/cuentas (api.routers.carteras); consumido por el dropdown de cuentas del frontend.

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
