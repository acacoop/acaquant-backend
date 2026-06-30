---
id: web.cmp.derivados-operar
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/derivados-operar.tsx
---

# web/components/derivados-operar

**Archivo:** `src/components/derivados-operar.tsx`

## Qué hace
Panel para operar un contrato de opción directo desde Derivados: order book L2 en vivo del instrumento elegido en la chain más un ticket de orden (BUY/SELL, LIMIT/MARKET, TIF). La cuenta es un campo del ticket; no hay selector global.

Conecta con: consume `/api/operar/order-book` (libro live), `/api/ordenes` (envío) y `/api/operar/bracket`; se invoca desde la vista de opciones de `web.cmp.derivados-view`.

## Usa / conecta con →
- [[api.routers.operar]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
