---
id: web.api.api.analitica.[...path]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\analitica\[...path]\route.ts
---

# web /api/analitica/[...path]  (proxy)

**Archivo:** `src\app\api\analitica\[...path]\route.ts`

## Qué hace
Proxy catch-all (GET y POST) del frontend Next hacia `/api/analitica/*` del backend FastAPI. Reenvía cualquier sub-path y querystring, inyecta las credenciales de Cloudflare Access + API key, y devuelve el JSON tal cual sin cachear (analíticas que cambian al ritmo de los trades).

Conecta con: front Next (vistas de analítica) → este route → backend `api/routers/analitica.py` en `api.acaquant.com`.

_Sin conexiones detectadas mecánicamente._
