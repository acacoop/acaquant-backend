---
id: web.cmp.derivados-agro-opciones
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\derivados-agro-opciones.tsx
---

# web/components/derivados-agro-opciones

**Archivo:** `src\components\derivados-agro-opciones.tsx`

## Qué hace
Cadena de opciones agro (calls y puts por strike y vencimiento) para el commodity seleccionado, con bid/offer/last/vol de cada pata y el futuro de referencia. Pollea cada 5 s; es la vista de solo-consulta de la chain.

Conecta con: consume el panel de opciones agro del backend (service `api.services.derivados_agro`, datos del motor `engines.motor_agro_opciones`); se monta en `web.cmp.derivados-agro-view`.

## Usa / conecta con →
- [[web.lib.use-poll]]  ·  _lib_
