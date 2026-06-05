---
id: web.cmp.descomposicion-tab
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\descomposicion-tab.tsx
---

# web/components/descomposicion-tab

**Archivo:** `src\components\descomposicion-tab.tsx`

## Qué hace
Tab de descomposición de retorno realizado por bono: atribuye el retorno total a carry, rolldown y cambio de tasa, para curvas tasa_fija o CER. Grafica las contribuciones con barras (recharts) y deja filtrar el rango de vencimientos con un dual-range.

Conecta con: consume `/api/analitica/descomposicion-retorno` y `/rolldown-esperado` (service `api.services.descomposicion_retorno`); usa el componente `web.cmp.dual-range`.

## Usa / conecta con →
- [[api.routers.analitica]]  ·  _module_
- [[web.lib.use-viewport-key]]  ·  _lib_
