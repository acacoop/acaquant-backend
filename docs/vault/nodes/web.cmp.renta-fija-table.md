---
id: web.cmp.renta-fija-table
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/renta-fija-table.tsx
---

# web/components/renta-fija-table

**Archivo:** `src/components/renta-fija-table.tsx`

## Qué hace
Tabla principal de bonos de Renta Fija con tabs por curva: tasa_fija, CER, soberanos, dólar-linked y libro (order book). Muestra precio, volumen, TEA/TNA, duration, vencimiento, etc.; usa curva_efectiva para mover los CER fijados a la pestaña tasa_fija. La pestaña libro abre el LibroPanel (profundidad de mercado del ticker).

Conecta con: presentacional — recibe data y flujos ya pulleados por renta-fija-live (origen Trading.MarketSnapshot/Curvas vía api.services.renta_fija). Embebe libro-panel para el order book live.

_Sin conexiones detectadas mecánicamente._
