---
id: web.cmp.futuros-dlr-curve-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/futuros-dlr-curve-chart.tsx
---

# web/components/futuros-dlr-curve-chart

**Archivo:** `src/components/futuros-dlr-curve-chart.tsx`

## Qué hace
Grafica la curva completa de futuros DLR (Dólar A3500) — precio vs días al vencimiento — como scatter + línea. Resaltea el contrato seleccionado en el watchlist; cualquier ticker "DLR/*" o "FUTUROS ROFEX" muestra la curva entera. Pollea cada 5s.

Conecta con: hace fetch a `/api/futuros-dlr` (datos de `Trading.FuturosDLRSnapshot` que alimenta `motor_futuros_dlr`). Lo usa `home-view` como chart alternativo a TradingView cuando el ticker es un DLR.

## Lo usan (backlinks) ←
- [[web.cmp.home-view]]  ·  _component_
