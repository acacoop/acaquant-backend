---
id: web.cmp.valuaciones-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\valuaciones-view.tsx
---

# web/components/valuaciones-view

**Archivo:** `src\components\valuaciones-view.tsx`

## Qué hace
Sub-vista PORTAFOLIO de Valuaciones: serie histórica de valuación de una cuenta (gráfico de línea) más una tabla mensual con valuación de cierre, depósitos, extracciones, flujo neto y los deltas bruto/real (performance neta de aportes y retiros). Permite exportar a XLSX.

Conecta con: pega a los endpoints de serie y mensual de valuaciones (api.routers.valuaciones → api.services.valuaciones), que computan sobre Valuaciones.AuM y flujos de caja por cuenta. Embebida en valuaciones-shell.

## Usa / conecta con →
- [[api.routers.valuaciones]]  ·  _module_
- [[web.cmp.download-button]]  ·  _component_
- [[web.lib.xlsx-export]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.cmp.valuaciones-shell]]  ·  _component_
