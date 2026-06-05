---
id: web.cmp.intraday-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/intraday-view.tsx
---

# web/components/intraday-view

**Archivo:** `src/components/intraday-view.tsx`

## Qué hace
Herramienta de sesión para consolidar operaciones intradía: el usuario sube un CSV de fills, se procesa 100% en el browser (filtra por Client ID, agrupa por especie/moneda/plazo, neteo de cantidad y turnover) y persiste en sessionStorage. No comparte entre usuarios.

Conecta con: no pega al backend — todo cálculo es client-side sobre el CSV cargado. Se monta como tab "INTRADAY" dentro de `operaciones-view`.

_Sin conexiones detectadas mecánicamente._
