---
id: web.cmp.dolar-mep-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/dolar-mep-shell.tsx
---

# web/components/dolar-mep-shell

**Archivo:** `src/components/dolar-mep-shell.tsx`

## Qué hace
Shell del módulo Dólar MEP: maneja el estado compartido entre las sub-tabs COMPRA y TRADING (rueda, monto ARS/USD, comisión, cuenta) y centraliza los polls de cotización y saldo para no duplicarlos. Trae las cuentas descubiertas una vez al montar.

Conecta con: renderiza `web.cmp.dolar-mep-compra-view` y `dolar-mep-venta-view`; lee cuentas de `jobs.descubrir_cuentas` (vía API), saldo de `/api/risk/account/saldo` y cotización MEP live.

## Usa / conecta con →
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
