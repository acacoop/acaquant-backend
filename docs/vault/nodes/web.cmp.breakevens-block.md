---
id: web.cmp.breakevens-block
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/breakevens-block.tsx
---

# web/components/breakevens-block

**Archivo:** `src/components/breakevens-block.tsx`

## Qué hace
Bloque completo de Breakevens CER/Lecap: combina el breakeven mensual implícito por par (scatter + línea) con la inflación esperada del REM acumulada por mes, para ver si el mercado pricea por encima o por debajo de las expectativas. Incluye serie histórica (`BreakevenHistDoc`) y metadatos del CER (settlement, mes de IPC).

Conecta con: consume los endpoints de breakevens y de REM acumulado (`/api/...`, services `derivados.py` y `rem.py`); lee `Trading.BreakevensHistorico`.

_Sin conexiones detectadas mecánicamente._
