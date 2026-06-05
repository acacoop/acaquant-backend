---
id: web.cmp.aunesa-aum-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\aunesa-aum-panel.tsx
---

# web/components/aunesa-aum-panel

**Archivo:** `src\components\aunesa-aum-panel.tsx`

## Qué hace
Panel de MANAGER → AUNESA: consulta la valuación (AuM) de una cuenta puntual a una fecha dada, mostrando posición por posición la cantidad, precio, valuación y el desvío entre la valuación reportada y la esperada (cantidad × precio). Herramienta de control/auditoría manual por `id_cuenta`.

Conecta con: pollea `GET /api/manager/aum?id_cuenta=&fecha=`; solo accesible al rol manager.

## Usa / conecta con →
- [[api.routers.manager]]  ·  _module_
