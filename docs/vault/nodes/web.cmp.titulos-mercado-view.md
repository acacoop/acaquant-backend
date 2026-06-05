---
id: web.cmp.titulos-mercado-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\titulos-mercado-view.tsx
---

# web/components/titulos-mercado-view

**Archivo:** `src\components\titulos-mercado-view.tsx`

## Qué hace
Vista de Back Office "Títulos / Mercado": agrega las operaciones por ticker mostrando qué hay que enviar y recibir (cantidades e importes), el neto y la cantidad de operaciones, con detalle desplegable por cuenta (op, plazo, importe, comprobante, moneda). Poltea cada 10s y permite exportar a XLSX.

Conecta con: pega a /api/back-office/titulos-mercado (api.routers.back_office → api.services.back_office_titulos). Datos de operaciones del custodio.

## Usa / conecta con →
- [[web.lib.use-poll]]  ·  _lib_
- [[web.lib.xlsx-export]]  ·  _lib_
