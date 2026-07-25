---
id: web.api.api.historico-curva
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/historico-curva/route.ts
---

# web /api/historico-curva  (proxy)

**Archivo:** `src/app/api/historico-curva/route.ts`

## Qué hace
Route handler de Next que proxea al backend la serie histórica de una curva de bonos (precio, TEA, TEM, duration, paridad por fecha). Recibe ?curva, valida que venga, y pega a /api/cotizaciones/historico/curva en el FastAPI. Marcado force-dynamic + no-store porque la última fecha disponible cambia con el live fallback del backend.
- Conecta con: backend FastAPI /api/cotizaciones/historico/curva (api.routers.cotizaciones); consumido por la vista de curvas en acaquant-web.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
