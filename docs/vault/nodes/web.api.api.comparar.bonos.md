---
id: web.api.api.comparar.bonos
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/comparar/bonos/route.ts
---

# web /api/comparar/bonos  (proxy)

**Archivo:** `src/app/api/comparar/bonos/route.ts`

## Qué hace
Devuelve el universo de bonos disponibles para el selector del comparador (pega a `/api/analitica/comparar/bonos`). Sin cache de edge; el cliente refetcha al montar (TTL backend 30s).

Conecta con: selector del tab "Comparar Inversión" del front → este route → backend `GET /api/analitica/comparar/bonos` (service `api/services/comparar_inversion.py`).

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
