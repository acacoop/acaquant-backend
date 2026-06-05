---
id: web.lib.estrategias
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src\lib\estrategias.ts
---

# web/lib/estrategias

**Archivo:** `src\lib\estrategias.ts`

## Qué hace
Motor de estrategias de opciones del lado del cliente — port en TypeScript de `dashboard/views/opciones.py`. Define los templates de estrategias (spreads, conos/cunas, ratios, backspreads, cóndor de hierro, venta de vol) y resuelve cada uno contra el snapshot de opciones (`OpcionDoc`): elige strikes líquidos, calcula prima neta all-in (incluye derecho de mercado 0,20% sobre prima), griegas netas, payoff a vencimiento, breakevens y la tabla de escenarios ±2% con teórico Black-Scholes (también porteado, con `normCdf` Abramowitz-Stegun).

Conecta con: consume el chain de opciones que sirve el backend (`api.services.opciones`, alimentado por `engines.options`); cálculo 100% en navegador, no le escribe a Mongo ni hace fetch propio; lo usa la vista de derivados/opciones del frontend.

## Lo usan (backlinks) ←
- [[web.cmp.costo-historico-chart]]  ·  _component_
- [[web.cmp.derivados-shell]]  ·  _component_
- [[web.cmp.derivados-view]]  ·  _component_
- [[web.cmp.escenarios-tabla]]  ·  _component_
- [[web.cmp.estrategias-tabla]]  ·  _component_
- [[web.cmp.opciones-table-compact]]  ·  _component_
- [[web.cmp.payoff-chart]]  ·  _component_
- [[web.cmp.post-trade-lab]]  ·  _component_
- [[web.view.derivados.view]]  ·  _view_
