---
id: web.cmp.operar-fci-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\operar-fci-view.tsx
---

# web/components/operar-fci-view

**Archivo:** `src\components\operar-fci-view.tsx`

## Qué hace
Vista para operar Fondos Comunes de Inversión: buscador de FCI, cotización de la cuotaparte (con precisión, mínimos, plazo) y suscripción/rescate por importe o por cuotapartes. Soporta deep-link (`?fci=`) para prefiltrar. Reusa la gestión de órdenes y cartera del dashboard.

Conecta con: pega a los endpoints de búsqueda/cotización de FCI y a `/api/ordenes` (vía componentes importados de `operar-dashboard-view`). Lo monta `operar-shell` (tab FCI).

## Usa / conecta con →
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
