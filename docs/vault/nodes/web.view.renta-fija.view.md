---
id: web.view.renta-fija.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/renta-fija/page.tsx
---

# web /renta-fija  (view)

**Archivo:** `src/app/renta-fija/page.tsx`

## Qué hace
Vista `/renta-fija` — terminal de renta fija. SSR inicial en paralelo de 9 datasets (renta fija, forwards, flujos, breakevens, históricos de breakevens/forwards, forwards-zscore, fair value tasa_fija y CER) desde `/api/cotizaciones/*` y `/api/titulos/flujos`. Carga rápida con el último snapshot; luego `RentaFijaLiveView` mantiene los datasets live con polling client-side.

Conecta con: backend `/api/cotizaciones/{renta-fija,forwards,breakevens,fair-value,...}`, `/api/titulos/flujos`; componente `RentaFijaLiveView`.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[web.cmp.renta-fija-live]]  ·  _component_
- [[web.lib.api]]  ·  _lib_
- [[web.lib.types]]  ·  _lib_
