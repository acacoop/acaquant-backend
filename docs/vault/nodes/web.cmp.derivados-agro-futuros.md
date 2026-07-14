---
id: web.cmp.derivados-agro-futuros
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\derivados-agro-futuros.tsx
---

# web/components/derivados-agro-futuros

**Archivo:** `src\components\derivados-agro-futuros.tsx`

## Qué hace
Tabla de futuros agro (Trigo / Maíz / Soja Rosario) por commodity: precio US$, bid/offer, apertura, cierre previo y variación intradía, con días al vencimiento. Pollea cada 5 s.

Conecta con: consume `/api/derivados-agro` (mismo endpoint que la pizarra; service `api.services.derivados_agro`, datos del motor `engines.motor_agro`); se monta en `web.cmp.derivados-agro-view`.

## Usa / conecta con →
- [[web.lib.use-poll]]  ·  _lib_
