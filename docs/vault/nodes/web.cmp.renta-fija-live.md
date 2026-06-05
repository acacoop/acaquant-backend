---
id: web.cmp.renta-fija-live
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/renta-fija-live.tsx
---

# web/components/renta-fija-live

**Archivo:** `src/components/renta-fija-live.tsx`

## Qué hace
Contenedor live de la pantalla de Renta Fija. Hace un único poll cada 5s al endpoint /snapshot-live (devuelve renta_fija + forwards + breakevens, cada bloque con su propio TTL de cache server-side) y reparte los datos a sus sub-bloques: tabla de renta fija, panel de forwards, curvas y breakevens. Unificar a un solo poll bajó ~70% la carga al backend.

Conecta con: pega al endpoint snapshot-live (api.routers.cotizaciones/renta_fija → api.services.renta_fija), alimentado por motor_curvas/forwards/breakevens vía Trading.MarketSnapshot. Compone renta-fija-table, forwards-panel, curvas-chart y breakevens-block.

## Lo usan (backlinks) ←
- [[web.view.renta-fija.view]]  ·  _view_
