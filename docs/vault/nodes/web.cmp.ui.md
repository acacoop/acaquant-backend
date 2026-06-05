---
id: web.cmp.ui
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/ui.tsx
---

# web/components/ui

**Archivo:** `src/components/ui.tsx`

## Qué hace
Módulo de utilidades compartidas de UI: reexporta el componente Panel, expone el placeholder Empty ("SIN DATOS — MERCADO CERRADO") y un set de formateadores comunes (shortTicker, fmtNum, fmtPrice, fmtVol, fmtPct, fmtHoraAR, etc.) usados en toda la app para mostrar precios, volúmenes y porcentajes con formato es-AR.

Conecta con: helpers puros sin estado ni API. Importado transversalmente por casi todas las vistas y tablas (renta-fija, titulos-mercado, valuaciones, etc.).

_Sin conexiones detectadas mecánicamente._
