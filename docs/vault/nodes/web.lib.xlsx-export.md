---
id: web.lib.xlsx-export
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/xlsx-export.ts
---

# web/lib/xlsx-export

**Archivo:** `src/lib/xlsx-export.ts`

## Qué hace
Helper para exportar tablas del frontend a XLSX con SheetJS, cargado de forma lazy (solo al hacer click en descargar, no en el load de la página). `exportToXlsx` recibe hojas con sus columnas tipadas (text/number/currency/percent/integer/date) y aplica el number-format de Excel por celda en locale es-AR, preservando el tipo nativo para que Excel pueda sumar/filtrar. Soporta título de metadata por hoja y `timestampSuffix` para nombrar el archivo.

Conecta con: utilidad de cliente, sin red; consume las rows ya armadas por las vistas (ej. AuM por cuenta/asset) y dispara la descarga en el browser via `XLSX.writeFile`.

## Lo usan (backlinks) ←
- [[web.cmp.aum-view]]  ·  _component_
- [[web.cmp.comercial-informe-view]]  ·  _component_
- [[web.cmp.comercial-operaciones-view]]  ·  _component_
- [[web.cmp.pnl-titulos-view]]  ·  _component_
- [[web.cmp.referidos-view]]  ·  _component_
- [[web.cmp.titulos-mercado-view]]  ·  _component_
- [[web.cmp.valuaciones-view]]  ·  _component_
