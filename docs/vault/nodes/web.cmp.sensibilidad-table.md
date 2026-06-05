---
id: web.cmp.sensibilidad-table
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\sensibilidad-table.tsx
---

# web/components/sensibilidad-table

**Archivo:** `src\components\sensibilidad-table.tsx`

## Qué hace
Tabla de sensibilidad de soberanos (globales/bonares): para cada bono muestra precio, TEA, duration, paridad y cuánto cobra en el horizonte, y una matriz de escenarios de TIR (precio objetivo + upside %) coloreada por retorno (gradiente verde→rojo). Modo absoluto vs relativo. Poltea cada 5 min.

Conecta con: pega al endpoint de sensibilidad (api.routers.analitica → api.services.sensibilidad), que recalcula precio objetivo por escenario de TIR sobre Trading.Curvas/MarketSnapshot. Se usa dentro de retorno-total-view.

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
