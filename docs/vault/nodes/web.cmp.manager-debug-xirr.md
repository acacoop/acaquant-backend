---
id: web.cmp.manager-debug-xirr
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\manager-debug-xirr.tsx
---

# web/components/manager-debug-xirr

**Archivo:** `src\components\manager-debug-xirr.tsx`

## Qué hace
Panel de debug del XIRR mensual de valuaciones (Manager → Valuaciones): desglosa mes a mes el cálculo de retorno de una cuenta — valor inicio/cierre, depósitos/extracciones, flujos individuales, el cashflow que entra al XIRR, TEA/TEM y la versión en USD (con MEP aplicado).

Conecta con: fetch a `/api/manager/valuaciones/debug` (respaldado por `api.services.valuaciones` + `quant.xirr`). Se monta dentro de `manager-view`.

## Usa / conecta con →
- [[api.routers.manager.valuaciones]]  ·  _module_
