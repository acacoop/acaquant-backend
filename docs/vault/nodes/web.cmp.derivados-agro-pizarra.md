---
id: web.cmp.derivados-agro-pizarra
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\derivados-agro-pizarra.tsx
---

# web/components/derivados-agro-pizarra

**Archivo:** `src\components\derivados-agro-pizarra.tsx`

## Qué hace
Pizarra del Pase Agro: por commodity muestra filas de pizarra, disponible y futuro (precio US$, pase, ARS, TNA en US$) más el dólar oficial mayorista live con su frescura (age/stale). Pollea cada 5 s y expone los precios manuales editados por la mesa.

Conecta con: consume `/api/derivados-agro` (service `api.services.derivados_agro`); lee el dólar oficial de `Valuaciones.DolarOficialLive`; alimenta el header de `web.cmp.derivados-agro-view`.

## Usa / conecta con →
- [[web.lib.use-poll]]  ·  _lib_
