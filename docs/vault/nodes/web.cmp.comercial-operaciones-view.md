---
id: web.cmp.comercial-operaciones-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\comercial-operaciones-view.tsx
---

# web/components/comercial-operaciones-view

**Archivo:** `src\components\comercial-operaciones-view.tsx`

## Qué hace
Vista COMERCIAL dentro de OPERACIONES: lente por operador. Header con selector de operador y KPIs (AuM gestionado, nº clientes, volumen MTD/YTD); a la izquierda gráfico de evolución + ficha del cliente, a la derecha tabla de clientes y su tenencia. Seleccionar un cliente re-scopea el gráfico y llena la ficha sin re-fetch. Embebe la sub-vista INFORME.

Conecta con: consume `GET /api/operaciones/comercial/*` (service `comercial.py`); monta `comercial-informe-view`. Ver docs/TABLERO_COMERCIAL.md.

## Usa / conecta con →
- [[api.routers.operaciones]]  ·  _module_
- [[web.lib.fmt-money]]  ·  _lib_
- [[web.lib.use-persisted-state]]  ·  _lib_
- [[web.lib.xlsx-export]]  ·  _lib_
