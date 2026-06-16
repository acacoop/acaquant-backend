---
id: web.lib.types-scanner
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src\lib\types-scanner.ts
---

# web/lib/types-scanner

**Archivo:** `src\lib\types-scanner.ts`

## Qué hace
Tipos TypeScript del módulo Scanner (Renta Variable). Define los contratos de las respuestas del backend: `CclLive` (KPI live de CCL para el shell), `PivotData`/`PivotFrame`/`PivotLevels` (pivot points en 4 timeframes sobre el subyacente USD) y los docs del scanner de CEDEARs (join de master categórico + snapshot live).

Conecta con: refleja lo que sirven `GET /api/scanner/*` (`api.routers.scanner` + `api.services.scanner`), que cruzan `Trading.Cedears`/`CedearsSnapshot` (vía `engines.motor_cedears`) y los pivots de `quant.pivot_points`; lo importan las vistas del Scanner en el frontend.

## Lo usan (backlinks) ←
- [[web.cmp.cedears-scanner-table]]  ·  _component_
- [[web.cmp.estrategia-shared]]  ·  _component_
- [[web.cmp.metricas-panel]]  ·  _component_
- [[web.cmp.pivot-points-panel]]  ·  _component_
- [[web.cmp.renta-variable-shell]]  ·  _component_
- [[web.cmp.scanner-view]]  ·  _component_
- [[web.cmp.ticker-chart-panel]]  ·  _component_
- [[web.view.renta-variable.view]]  ·  _view_
