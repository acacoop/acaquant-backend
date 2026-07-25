---
id: web.cmp.aum-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/aum-view.tsx
---

# web/components/aum-view

**Archivo:** `src/components/aum-view.tsx`

## Qué hace
Vista AuM: muestra la evolución histórica del activo bajo administración (área temporal por emisor) y el detalle de tenencias por unidad/cuenta, con un bloque dedicado a Tasa Fija (cobro proyectado por ticker y por cuenta hasta vencimiento). Permite exportar a XLSX.

Conecta con: consume los endpoints de AuM/portfolio (`/api/...` sobre `Valuaciones.AuM`, alimentada por `jobs/aum.py`); grafica con recharts y exporta vía `xlsx-export`.

## Usa / conecta con →
- [[api.routers.me]]  ·  _module_
- [[web.cmp.download-button]]  ·  _component_
- [[web.lib.xlsx-export]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.cmp.valuaciones-shell]]  ·  _component_
- [[web.view.aum.view]]  ·  _view_
