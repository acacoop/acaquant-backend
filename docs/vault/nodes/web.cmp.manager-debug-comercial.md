---
id: web.cmp.manager-debug-comercial
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\manager-debug-comercial.tsx
---

# web/components/manager-debug-comercial

**Archivo:** `src\components\manager-debug-comercial.tsx`

## Qué hace
Panel de diagnóstico del informe comercial (Manager → Diagnóstico): elegís operador y/o segmento y muestra, por cuenta, cuántas operaciones reconoce y qué volúmenes/aranceles, auditando el ticket promedio.

Conecta con: fetch a `/api/operaciones/comercial/operadores` (lista de operadores) y `/api/manager/checks/debug-comercial` (cruza `CashFlow.NegocioMovimientos` + `Clientes.Comitentes`). Se monta dentro de `manager-view`.

## Usa / conecta con →
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
