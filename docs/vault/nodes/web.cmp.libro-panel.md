---
id: web.cmp.libro-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/libro-panel.tsx
---

# web/components/libro-panel

**Archivo:** `src/components/libro-panel.tsx`

## Qué hace
Panel de "libro" / time & sales por instrumento de renta fija: dropdown buscable de tickers (ordenados por nominales operados) y, al seleccionar uno, la lista de trades recientes (precio, tamaño, hora, lado).

Conecta con: recibe la grilla de renta fija por props y hace fetch de los trades del ticker elegido (datos de `Trading.TimeSales`). Se usa dentro de la vista de Renta Fija.

_Sin conexiones detectadas mecánicamente._
