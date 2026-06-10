---
id: web.cmp.pnl-totales-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/pnl-totales-view.tsx
---

# web/components/pnl-totales-view

**Archivo:** `src/components/pnl-totales-view.tsx`

## Qué hace
Vista de PnL consolidado de TODAS las cuentas. Tabla con una fila por (cuenta, ticker) más los totales agregados (costo, valor, PnL no realizado / pasivo / realizado del día / total), con switch ARS/USD. Reutiliza el detalle de posiciones y los formateadores de pnl-titulos-view, y embebe la sub-vista por-cuenta.

Conecta con: pega a /api/aum-pnl-todas, que sirve el PnL precalculado de Valuaciones.PnLTotalesCache (job jobs.pnl_totales_precompute) vía api.services.pnl.

## Usa / conecta con →
- [[web.cmp.pnl-titulos-view]]  ·  _component_
- [[web.cmp.por-cuenta-view]]  ·  _component_

## Lo usan (backlinks) ←
- [[web.cmp.valuaciones-shell]]  ·  _component_
