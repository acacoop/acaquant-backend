---
id: web.cmp.cedears-scanner-table
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/cedears-scanner-table.tsx
---

# web/components/cedears-scanner-table

**Archivo:** `src/components/cedears-scanner-table.tsx`

## Qué hace
Tabla del Scanner de Renta Variable con switch CEDEAR/ADR. En CEDEAR muestra precio BYMA en ARS y métricas live del `motor_cedears`; en ADR muestra el precio NYSE del subyacente en USD y retornos EOD (1d/7d/MTD/YTD). Todas las columnas son ordenables; default top-movers arriba.

Conecta con: recibe `CedearScannerRow[]` desde el scanner (service `scanner.py`, fuentes `motor_cedears` live + `Trading.PreciosAcciones` EOD); emite selección de ticker al contenedor.

_Sin conexiones detectadas mecánicamente._
