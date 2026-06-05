---
id: web.cmp.aunesa-posicion-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\aunesa-posicion-panel.tsx
---

# web/components/aunesa-posicion-panel

**Archivo:** `src\components\aunesa-posicion-panel.tsx`

## Qué hace
Panel de MANAGER → AUNESA que consulta la posición ("Acumulado") cruda que reporta el custodio Aunesa para una cuenta a una fecha de liquidación (por defecto T+2 hábil). Lista los títulos con cantidad y precio tal cual los devuelve Aunesa, sin transformar — herramienta de verificación contra la tenencia interna.

Conecta con: pollea `GET /api/manager/aunesa/posicion?id_cuenta=&desde=`; cliente `core/aunesa.py`. Solo manager.

## Usa / conecta con →
- [[api.routers.manager.aunesa]]  ·  _module_
