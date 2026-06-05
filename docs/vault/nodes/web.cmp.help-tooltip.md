---
id: web.cmp.help-tooltip
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/help-tooltip.tsx
---

# web/components/help-tooltip

**Archivo:** `src/components/help-tooltip.tsx`

## Qué hace
Dos componentes de ayuda inline reutilizables: `HelpTooltip` (un `?` chiquito con tooltip al hover, para explicar un label puntual) y `TableHelp` (un `?` que abre un modal con el glosario completo de una tabla, con backdrop y scroll). Puro UI, sin fetch.

Conecta con: no toca backend ni Mongo. Lo importan tablas y labels de muchas vistas (ej. `opciones-table-compact` usa `TableHelp`) para documentar columnas/métricas.

_Sin conexiones detectadas mecánicamente._
