---
id: web.cmp.escenarios-tabla
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/escenarios-tabla.tsx
---

# web/components/escenarios-tabla

**Archivo:** `src/components/escenarios-tabla.tsx`

## Qué hace
Tabla de escenarios de P&L para una estrategia de opciones: mueve el spot en pasos de ±2% y muestra el resultado de las patas a vencimiento, dada la tasa y el costo del armado. Calcula todo en el cliente.

Conecta con: usa `buildScenarios` de `lib/estrategias`; se monta como tab de detalle dentro de `web.cmp.derivados-view`. No pega a la API.

## Usa / conecta con →
- [[web.lib.estrategias]]  ·  _lib_
