---
id: web.cmp.derivados-agro-estrategias
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/derivados-agro-estrategias.tsx
---

# web/components/derivados-agro-estrategias

**Archivo:** `src/components/derivados-agro-estrategias.tsx`

## Qué hace
Tabla de estrategias con opciones agro (put sintético / long put) sobre Trigo, Maíz y Soja de Rosario. Muestra la cadena de strikes (call/put con bid/offer/last/vol) por vencimiento y grafica el payoff de la estrategia armada con recharts.

Conecta con: consume el endpoint backend de paneles de opciones agro (`/api/derivados/agro`, service `api.services.derivados_agro` / motor `engines.motor_agro_opciones`); se monta dentro de `web.cmp.derivados-agro-view`.

_Sin conexiones detectadas mecánicamente._
