---
id: web.cmp.date-picker
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/date-picker.tsx
---

# web/components/date-picker

**Archivo:** `src/components/date-picker.tsx`

## Qué hace
Date picker compacto con calendario inline navegable (no usa el `<input type=date>` nativo), con tope por defecto en hoy (ART, UTC-3) y soporte para acotar el rango seleccionable vía `min`/`max`. Exporta además helpers de fecha reutilizables (`todayART`, `parseISO`, `toISO`, `addDays`, `fmtDisplay`). Extraído del panel Aunesa para reuso en toda la app.

Conecta con: componente puro de UI; lo usan vistas como `agro-view`, `aranceles-view`, `cashflow-view` para sus toolbars de rango.

_Sin conexiones detectadas mecánicamente._
