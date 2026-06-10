---
id: web.cmp.opcion-historico-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/opcion-historico-chart.tsx
---

# web/components/opcion-historico-chart

**Archivo:** `src/components/opcion-historico-chart.tsx`

## Qué hace
Histórico de precio de un contrato de opción individual (call o put GGAL): plotea `last` vs tiempo sobre las operaciones de los últimos 21 días, con línea de referencia opcional del último precio live. Usa índice como eje X para no abrir huecos en fines de semana.

Conecta con: fetch a `/api/cotizaciones/historico/opciones?instrumento=X` (lee `Opciones.Data`). Se linkea al contrato seleccionado en la tabla de opciones.

## Usa / conecta con →
- [[api.routers.cotizaciones]]  ·  _module_
- [[web.lib.use-viewport-key]]  ·  _lib_
