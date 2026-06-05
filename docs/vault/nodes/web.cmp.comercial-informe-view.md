---
id: web.cmp.comercial-informe-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\comercial-informe-view.tsx
---

# web/components/comercial-informe-view

**Archivo:** `src\components\comercial-informe-view.tsx`

## Qué hace
Sub-vista INFORME del Tablero Comercial: reporte GLOBAL de la mesa (no por operador), con 4 cuadrantes — ranking de comerciales por volumen y aranceles, distribución por segmento patrimonial, aranceles por segmento y detalle de clientes/operaciones. Exporta a XLSX.

Conecta con: consume `GET /api/operaciones/comercial/informe` e `informe-segmento` (service `comercial.py`); datos de `Clientes.Comitentes`, `CashFlow.NegocioMovimientos` y `Valuaciones.AuM`. Ver docs/TABLERO_COMERCIAL.md.

## Usa / conecta con →
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.fmt-money]]  ·  _lib_
- [[web.lib.xlsx-export]]  ·  _lib_
