---
id: web.cmp.dolar-mep-timesales-chart
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\dolar-mep-timesales-chart.tsx
---

# web/components/dolar-mep-timesales-chart

**Archivo:** `src\components\dolar-mep-timesales-chart.tsx`

## Qué hace
Gráfico de la serie intradía del MEP por minuto (last close) para la rueda elegida (CI/24hs). Toda la lógica de agregación vive en el backend; el componente solo dibuja con recharts y pollea cada 5 s.

Conecta con: consume `/api/operativa/mep/timesales?rueda=...` (service `api.services.operativa_mep`); se monta dentro de `web.cmp.dolar-mep-board`.

## Usa / conecta con →
- [[api.routers.operativa]]  ·  _module_
