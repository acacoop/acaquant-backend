---
id: web.cmp.derivados-agro-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/derivados-agro-view.tsx
---

# web/components/derivados-agro-view

**Archivo:** `src/components/derivados-agro-view.tsx`

## Qué hace
Contenedor del módulo Agro (/agro): orquesta el selector único de commodity (Trigo/Maíz/Soja) y arma el layout — pizarra arriba, futuros + cadena de opciones a la izquierda, estrategias a la derecha. Comparte el commodity elegido entre los sub-paneles.

Conecta con: compone `web.cmp.derivados-agro-pizarra`, `-futuros`, `-opciones` y `-estrategias`; todos consumen los endpoints `/api/derivados/agro` del backend.

_Sin conexiones detectadas mecánicamente._
