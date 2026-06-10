---
id: web.cmp.comercial-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/comercial-panel.tsx
---

# web/components/comercial-panel

**Archivo:** `src/components/comercial-panel.tsx`

## Qué hace
Tablero Comercial v1 (solo manager): tabla por operador con el conteo de cuentas según estado comercial (activas / enfriándose / dormidas / nuevas / sin segmentar) y el AuM total de cada operador, marcando las cuentas huérfanas. Es la vista de supervisión de la cartera por comercial.

Conecta con: pollea `GET /api/manager/comercial/operadores` (service `comercial.py`); datos de `Clientes.Comitentes` + `Valuaciones.AuM`. Ver docs/TABLERO_COMERCIAL.md.

## Usa / conecta con →
- [[api.routers.manager.comercial]]  ·  _module_
