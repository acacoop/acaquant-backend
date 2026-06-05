---
id: web.cmp.monitor-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\monitor-view.tsx
---

# web/components/monitor-view

**Archivo:** `src\components\monitor-view.tsx`

## Qué hace
Vista MONITOR (Módulo 2 de la Mesa de Estrategia, Renta Variable). El usuario carga un book (lista de posiciones ticker/notional/dirección) y devuelve exposición por sector/región, concentración (top5, HHI), riesgo agregado (vol anual, VaR 1d 95%, exposición a SPY/QQQ) y contribución de riesgo por activo.

Conecta con: fetch a `/api/scanner/book-analysis` (respaldado por `api.services.rv_motor` / scanner). Vive en el tab Renta Variable.

## Usa / conecta con →
- [[api.routers.scanner]]  ·  _module_
