---
id: web.cmp.dolar-mep-compra-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/dolar-mep-compra-view.tsx
---

# web/components/dolar-mep-compra-view

**Archivo:** `src/components/dolar-mep-compra-view.tsx`

## Qué hace
Vista de compra de dólar MEP: ofrece la operativa instantánea (form EJECUTAR con monto ARS, comisión y cuenta) que dispara las 2 patas BUY AL30 + SELL AL30D, más la tabla de operativas del día. El shell le inyecta rueda/monto/cotización/saldo por props.

Conecta con: postea a `/api/operativa/mep/compra` (service `api.services.operativa_mep`), lee saldo de `/api/risk/account/saldo` y cotización live; usa `web.cmp.dolar-mep-board` y `dolar-mep-detalle-drawer`.

_Sin conexiones detectadas mecánicamente._
