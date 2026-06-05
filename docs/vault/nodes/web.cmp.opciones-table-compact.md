---
id: web.cmp.opciones-table-compact
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/opciones-table-compact.tsx
---

# web/components/opciones-table-compact

**Archivo:** `src/components/opciones-table-compact.tsx`

## Qué hace
Tabla compacta de la cadena de opciones GGAL (CALL/PUT), ordenada por valor esperado, con columnas de variación intradía/1D, spread de puntas, IV y griegas (delta, gamma, theta, vega) y volumen. Glosario embebido por columna y selección de contrato para linkear los charts.

Conecta con: recibe la chain por props (datos de `Opciones.Data` vía router `cotizaciones`/`opciones`); emite `onSelect` para alimentar `opcion-historico-chart` y `griegas-historico-chart`. Usa `TableHelp`. Vive en el módulo de opciones / derivados.

_Sin conexiones detectadas mecánicamente._
