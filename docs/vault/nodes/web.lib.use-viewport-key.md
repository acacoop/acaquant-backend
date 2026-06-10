---
id: web.lib.use-viewport-key
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/use-viewport-key.ts
---

# web/lib/use-viewport-key

**Archivo:** `src/lib/use-viewport-key.ts`

## Qué hace
Hook `useViewportKey` — devuelve un contador que incrementa cada vez que cambia algo del entorno visual: resize, cambio de DPR, movimiento entre monitores, vuelta de foco al tab o `ResizeObserver` del `<html>`. Se usa como `key` en el `ResponsiveContainer` de recharts para forzar un remount, porque recharts a veces mide 0 en transiciones y no se recupera solo.

Conecta con: hook de cliente puro, sin red ni datos de negocio; lo consumen los componentes de gráficos (recharts) del frontend.

## Lo usan (backlinks) ←
- [[web.cmp.breakevens-block]]  ·  _component_
- [[web.cmp.canje-tab]]  ·  _component_
- [[web.cmp.costo-historico-chart]]  ·  _component_
- [[web.cmp.curvas-chart]]  ·  _component_
- [[web.cmp.descomposicion-tab]]  ·  _component_
- [[web.cmp.flujo-vs-aum-view]]  ·  _component_
- [[web.cmp.forwards-panel]]  ·  _component_
- [[web.cmp.griegas-historico-chart]]  ·  _component_
- [[web.cmp.opcion-historico-chart]]  ·  _component_
- [[web.cmp.payoff-chart]]  ·  _component_
