---
id: web.cmp.por-cuenta-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/por-cuenta-view.tsx
---

# web/components/por-cuenta-view

**Archivo:** `src/components/por-cuenta-view.tsx`

## Qué hace
Vista resumen con una fila por cuenta: valor en ARS y USD, base 100 (performance indexada) y PnL acumulado. Tiene filtro por tipo de cuenta (todas / accionistas / sin accionistas / cooperativas / productores), ordenamiento por columnas y descarta cuentas con saldo "muerto". Es la sub-pestaña agregada del PnL total.

Conecta con: pega a /api/valuaciones/consolidado, que reusa el cálculo de la tabla mensual de portafolio y lee Valuaciones.ConsolidadoCuentas (job jobs.consolidado_cuentas). Embebida en pnl-totales-view.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.cmp.pnl-titulos-view]]  ·  _component_

## Lo usan (backlinks) ←
- [[web.cmp.pnl-totales-view]]  ·  _component_
