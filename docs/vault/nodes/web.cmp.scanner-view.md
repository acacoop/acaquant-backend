---
id: web.cmp.scanner-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\scanner-view.tsx
---

# web/components/scanner-view

**Archivo:** `src\components\scanner-view.tsx`

## Qué hace
Vista Scanner (pestaña de /renta-variable). Layout: mitad izquierda la tabla de CEDEARs (con switch CEDEAR/ADR y KPI CCL inline), mitad derecha dividida en panel de pivot points (arriba) y chart/retornos del ticker (abajo). Click en una fila selecciona el ticker y recalcula los paneles. Poltea CEDEARs y CCL cada 10s.

Conecta con: pega a /api/scanner/cedears y /api/scanner/ccl (api.routers.scanner → api.services.scanner, datos del motor_cedears). Embebe cedears-scanner-table, pivot-points-panel y ticker-chart-panel.

## Usa / conecta con →
- [[api.routers.scanner]]  ·  _module_
- [[web.lib.types-scanner]]  ·  _lib_
- [[web.lib.use-poll]]  ·  _lib_
