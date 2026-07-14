---
id: web.api.api.derivados-agro.camara
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\derivados-agro\camara\route.ts
---

# web /api/derivados-agro/camara  (proxy)

**Archivo:** `src\app\api\derivados-agro\camara\route.ts`

## Qué hace
Lista los cereales de la Cámara Arbitral de Cereales de Rosario con sus precios ARS y USD manuales (pega a `/api/derivados/agro/camara`). Sin cache para reflejar ediciones en tiempo real.

Conecta con: panel Cámara de la vista agro del front → este route → backend `GET /api/derivados/agro/camara` (service `api/services/camara_cereales.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
