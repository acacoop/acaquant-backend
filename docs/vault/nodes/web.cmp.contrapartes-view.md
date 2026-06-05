---
id: web.cmp.contrapartes-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/contrapartes-view.tsx
---

# web/components/contrapartes-view

**Archivo:** `src/components/contrapartes-view.tsx`

## Qué hace
Vista CONTRAPARTES: grafica el flujo bruto operado agrupado por contraparte/segmento y por mes, separando ARS y USD por color. Cruza los documentos de flujo con el maestro de contrapartes para etiquetar cada operación con su nombre/grupo/segmento.

Conecta con: consume los endpoints de operaciones (flujo de contrapartes) sobre `CashFlow.Contrapartes` / `NegocioMovimientos`; alimentada por `jobs/flujo_contrapartes.py`.

_Sin conexiones detectadas mecánicamente._
