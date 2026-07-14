---
id: web.cmp.operaciones-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\operaciones-view.tsx
---

# web/components/operaciones-view

**Archivo:** `src\components\operaciones-view.tsx`

## Qué hace
Vista contenedora de /operaciones. Organiza en tabs: OPERACIONES, ARANCELES, AGRO, DEPÓSITOS & EXTRACCIONES e INTRADAY. Usa keep-alive (cada tab se monta la primera vez y luego se oculta con CSS) para que cambiar de pestaña sea instantáneo sin re-fetch.

Conecta con: compone `OpsView`, `ArancelesView`, `AgroView`, `CashFlowView` e `IntradayView` — cada uno con su endpoint. Gateada por el módulo `operaciones` en `header`.

## Usa / conecta con →
- [[web.lib.use-persisted-state]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.operaciones.view]]  ·  _view_
