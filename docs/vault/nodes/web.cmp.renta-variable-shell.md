---
id: web.cmp.renta-variable-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/renta-variable-shell.tsx
---

# web/components/renta-variable-shell

**Archivo:** `src/components/renta-variable-shell.tsx`

## Qué hace
Shell (contenedor con tabs) de la pantalla /renta-variable. Tres pestañas: SCANNER (CEDEARs: master + snapshot live), ESTRATEGIA (Mesa de Estrategia: análisis de un trade individual + hedging) y MONITOR (análisis de book / exposición). Recibe los datos iniciales del scanner y el CCL para hidratar la vista sin esperar el primer fetch.

Conecta con: orquesta scanner-view, estrategia-view y monitor-view. Los datos iniciales vienen del SSR de la page /renta-variable (api.routers.scanner sobre el motor_cedears / Trading.PreciosAcciones).

## Lo usan (backlinks) ←
- [[web.view.renta-variable.view]]  ·  _view_
