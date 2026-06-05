---
id: web.cmp.flujo-vs-aum-view
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/flujo-vs-aum-view.tsx
---

# web/components/flujo-vs-aum-view

**Archivo:** `src/components/flujo-vs-aum-view.tsx`

## Qué hace
Vista que cruza, por contraparte y mes, el AuM (línea) contra el flujo de negocio bruto (barras pos/neg) para ver si los movimientos acompañan o no la evolución del patrimonio. Filtra el rango temporal con un dual-range y grafica con recharts.

Conecta con: consume el endpoint de flujo-vs-AuM del backend (cruza `Valuaciones.AuM` con `CashFlow.NegocioMovimientos`); usa el componente `web.cmp.dual-range`.

_Sin conexiones detectadas mecánicamente._
