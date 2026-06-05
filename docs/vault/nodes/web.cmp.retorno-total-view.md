---
id: web.cmp.retorno-total-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\retorno-total-view.tsx
---

# web/components/retorno-total-view

**Archivo:** `src\components\retorno-total-view.tsx`

## Qué hace
Vista principal de /retorno: serie histórica de retorno total por curva, indexada base 100, con MEP/oficial y calendario de cobros (cupones + amortizaciones) por ticker. Incluye selector de rango (DualRange) y pestañas de análisis: Sensibilidad, Canje, Descomposición de retorno y Comparar Inversión.

Conecta con: pega a los endpoints de retorno/serie histórica (api.routers.analitica / cotizaciones → services de renta_fija, canje, descomposicion_retorno, sensibilidad, comparar_inversion). Compone sensibilidad-table, canje-tab, descomposicion-tab y comparar-inversion-view.

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
- [[web.lib.use-viewport-key]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.retorno.view]]  ·  _view_
