---
id: web.cmp.post-trade-lab
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\post-trade-lab.tsx
---

# web/components/post-trade-lab

**Archivo:** `src\components\post-trade-lab.tsx`

## Qué hace
Laboratorio post-trade de opciones: dada una posición ya tomada (un contrato o una estrategia entera) con su costo de entrada, proyecta el P&L a futuro repreciando con Black-Scholes en una matriz precio × tiempo (theta decay + direccionalidad), permite un shock de volatilidad implícita y marca los niveles clave. Responde "ya entré, ¿qué me pasa los próximos días?".

Conecta con: cálculo puro en el cliente vía lib/estrategias (bsPrice). Sin llamadas a la API; recibe patas, spot, tasa y vencimiento desde la vista de estrategia.

## Usa / conecta con →
- [[web.lib.estrategias]]  ·  _lib_
