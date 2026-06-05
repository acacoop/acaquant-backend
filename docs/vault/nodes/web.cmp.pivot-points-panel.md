---
id: web.cmp.pivot-points-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/pivot-points-panel.tsx
---

# web/components/pivot-points-panel

**Archivo:** `src/components/pivot-points-panel.tsx`

## Qué hace
Panel "Métricas" del Scanner de Renta Variable. Dos niveles de tabs: ZONAS (pivot points Floor Trader: PP/R1-3/S1-3 con distancia % al last) y VOLATILIDAD & BETA (beta/alpha/correlación vs SPY y QQQ, vol realizada, z-score de hoy). Para ZONAS hay sub-tabs por período (diario/semanal/mensual/anual). Re-fetchea al cambiar el ticker; las stats son lazy.

Conecta con: pega a /api/scanner/pivots/{ticker} y /api/scanner/stats/{ticker} (router api.scanner → service api.services.scanner), computados sobre Trading.PreciosAcciones. Lo embebe scanner-view.

_Sin conexiones detectadas mecánicamente._
