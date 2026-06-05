---
id: web.cmp.dolar-mep-detalle-drawer
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/dolar-mep-detalle-drawer.tsx
---

# web/components/dolar-mep-detalle-drawer

**Archivo:** `src/components/dolar-mep-detalle-drawer.tsx`

## Qué hace
Drawer lateral con el detalle de una operativa MEP ejecutada: datos del wrapper (cuenta, rueda, actor, monto, MEP inicial, status) y las métricas calculadas post-trade (precios efectivos de cada pata, USD/ARS operados, MEP efectivo y costo al cliente, slippage).

Conecta con: consume el endpoint de detalle de operativa MEP del backend (service `api.services.operativa_mep`); se abre desde las tablas de `web.cmp.dolar-mep-compra-view` / `dolar-mep-venta-view`.

_Sin conexiones detectadas mecánicamente._
