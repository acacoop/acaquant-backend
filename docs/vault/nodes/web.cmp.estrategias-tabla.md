---
id: web.cmp.estrategias-tabla
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\estrategias-tabla.tsx
---

# web/components/estrategias-tabla

**Archivo:** `src\components\estrategias-tabla.tsx`

## Qué hace
Tabla de estrategias de opciones GGAL precalculadas por strike: deja elegir strike (con ATM marcado), filtrar por categoría de estrategia y seleccionar una fila para ver su detalle. Solo muestra strikes con liquidez.

Conecta con: recibe las filas calculadas con `lib/estrategias` (CATEGORIAS, EstrategiaRow); se monta dentro de `web.cmp.derivados-view`. No pega a la API directamente.

## Usa / conecta con →
- [[web.lib.estrategias]]  ·  _lib_
