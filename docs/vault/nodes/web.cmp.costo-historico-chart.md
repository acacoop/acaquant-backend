---
id: web.cmp.costo-historico-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/costo-historico-chart.tsx
---

# web/components/costo-historico-chart

**Archivo:** `src/components/costo-historico-chart.tsx`

## Qué hace
Gráfico histórico del costo de una estrategia de opciones (combinación de legs) a lo largo del tiempo, bucketeado por minutos. Superpone en un segundo eje el spot del subyacente GGAL (switch ARS/ADR) y marca el costo live actual. Sirve a la vista de armado de estrategias de opciones.

Conecta con: recibe las `legs` de la estrategia y hace fetch del histórico de costo y del spot (`Opciones.VR-GGal`); endpoints del módulo opciones (service `opciones.py`).

_Sin conexiones detectadas mecánicamente._
