---
id: web.api.api.derivados-agro.opciones.[commodity]
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/derivados-agro/opciones/[commodity]/route.ts
---

# web /api/derivados-agro/opciones/[commodity]  (proxy)

**Archivo:** `src/app/api/derivados-agro/opciones/[commodity]/route.ts`

## Qué hace
Proxy live del panel de opciones agro por commodity (trigo/maíz/soja): reenvía el `commodity` de la URL a `/api/derivados/agro/opciones/{commodity}` y devuelve sin cache para que el polling reciba siempre el último snapshot (bid/offer/last refresh ~5s).

Conecta con: panel de opciones agro del front → este route → backend `GET /api/derivados/agro/opciones/{commodity}` (motor `engines/motor_agro_opciones.py`).

## Usa / conecta con →
- [[web.lib.api]]  ·  _lib_
