---
id: web.api.api.comparar
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\comparar\route.ts
---

# web /api/comparar  (proxy)

**Archivo:** `src\app\api\comparar\route.ts`

## Qué hace
Proxy del comparador de inversiones: propaga la querystring a `/api/analitica/comparar` del backend y devuelve sin cache (precios y MEP se mueven cada ~10s).

Conecta con: tab "Comparar Inversión" del front → este route → backend `GET /api/analitica/comparar` (service `api/services/comparar_inversion.py`).

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
