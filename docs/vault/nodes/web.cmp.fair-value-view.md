---
id: web.cmp.fair-value-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\fair-value-view.tsx
---

# web/components/fair-value-view

**Archivo:** `src\components\fair-value-view.tsx`

## Qué hace
Tabla del módulo Fair Value relativo intra-curva (tasa_fija / CER): lista bonos con su TEA observada, residuo en bps y z-scores (temporal vs su historia y estático vs la curva), coloreados barato/caro. Ordenable y polleada cada 90 s.

Conecta con: consume el endpoint Fair Value del backend (service `api.services.fair_value`, datos del job `jobs.fair_value`); abre el detalle histórico en `web.cmp.fair-value-modal`.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.types]]  ·  _lib_
- [[web.lib.use-poll]]  ·  _lib_
