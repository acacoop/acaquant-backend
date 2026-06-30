---
id: web.cmp.griegas-historico-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/griegas-historico-chart.tsx
---

# web/components/griegas-historico-chart

**Archivo:** `src/components/griegas-historico-chart.tsx`

## Qué hace
Gráfico de evolución diaria de las griegas (delta, gamma, vega, theta, IV) de un contrato de opción GGAL. Un solo chart con selector de griega; usa índice como eje X para no abrir huecos en fines de semana.

Conecta con: hace fetch a `/api/cotizaciones/griegas/opciones?instrumento=X` (rollup diario de `Opciones.DataHistorica`). Se linkea al contrato seleccionado en la tabla OPCIONES GGAL del módulo de opciones.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.use-viewport-key]]  ·  _lib_
