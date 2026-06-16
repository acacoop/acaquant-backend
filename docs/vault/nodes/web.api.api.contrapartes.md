---
id: web.api.api.contrapartes
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\contrapartes\route.ts
---

# web /api/contrapartes  (proxy)

**Archivo:** `src\app\api\contrapartes\route.ts`

## Qué hace
Arma la vista de contrapartes: en paralelo pide el flujo de operaciones de los últimos ~2 años (`/api/operaciones/flujo`) y el padrón de contrapartes (`/api/cuentas/contrapartes`), y los devuelve juntos. Trae PII → `Cache-Control: private, no-store`.

Conecta con: vista de contrapartes del front → este route → backend `api/routers/operaciones.py` + `api/routers/cuentas.py`.

## Usa / conecta con →
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
