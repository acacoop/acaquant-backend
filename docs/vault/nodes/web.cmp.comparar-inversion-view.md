---
id: web.cmp.comparar-inversion-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/comparar-inversion-view.tsx
---

# web/components/comparar-inversion-view

**Archivo:** `src/components/comparar-inversion-view.tsx`

## Qué hace
Tab "Comparar Inversión" dentro de /retorno: compara dos bonos de `Trading.Curvas` lado a lado para un monto y moneda dados. Header con monto/moneda/selectores de bono; debajo, tabla de métricas a la izquierda y gráfico de cupones (flujos escalados al monto invertido) a la derecha.

Conecta con: consume el endpoint de comparar inversión (service `comparar_inversion.py`), que lee `Trading.Curvas`. Ver memoria project_comparar_inversion_wip.

_Sin conexiones detectadas mecánicamente._
