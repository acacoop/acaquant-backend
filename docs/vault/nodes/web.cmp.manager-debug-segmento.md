---
id: web.cmp.manager-debug-segmento
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/manager-debug-segmento.tsx
---

# web/components/manager-debug-segmento

**Archivo:** `src/components/manager-debug-segmento.tsx`

## Qué hace
Panel "DEBUG SEGMENTO" (Manager → Validaciones): auditoría paso a paso del cálculo de `nivel_3` (segmento patrimonial) de una cuenta puntual — muestra cupo crudo, tipo de cambio usado (MEP o UVA), la conversión, y compara el nivel_3 guardado en Mongo vs el recalculado ahora, sin entrar a Mongo.

Conecta con: fetch a `/api/manager/comercial/debug-segmento?id_cuenta=X` (respaldado por `api.services.segmentacion` sobre `Clientes.Comitentes`). Se monta dentro de `manager-view`.

## Usa / conecta con →
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.comercial]]  ·  _module_
