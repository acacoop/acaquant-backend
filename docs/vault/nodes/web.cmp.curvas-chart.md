---
id: web.cmp.curvas-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\curvas-chart.tsx
---

# web/components/curvas-chart

**Archivo:** `src\components\curvas-chart.tsx`

## Qué hace
Gráfico principal de curvas de renta fija: dibuja los bonos (scatter de TEA/TEM/paridad/duration por ticker) más la línea de la curva ajustada y las tasas forward asociadas, con histórico por ticker. Embebe la vista de Fair Value relativo intra-curva.

Conecta con: consume los endpoints de renta fija / forwards / fair value (services `renta_fija.py`, `derivados.py`, `fair_value.py`), que leen `Trading.MarketSnapshot`, `Trading.Curvas` y los snapshots de forwards. Monta `fair-value-view`.

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
- [[web.lib.types]]  ·  _lib_
- [[web.lib.use-viewport-key]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.cmp.renta-fija-live]]  ·  _component_
