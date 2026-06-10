---
id: web.view.renta-variable.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/renta-variable/page.tsx
---

# web /renta-variable  (view)

**Archivo:** `src/app/renta-variable/page.tsx`

## Qué hace
Vista `/renta-variable` — módulo de renta variable (Scanner CEDEARs + Mesa de Estrategia). SSR en paralelo del scanner de CEDEARs (`/api/scanner/cedears`, TTL 5s) y el CCL live (`/api/scanner/ccl`) para el KPI del shell; el polling client (10s) hace la lectura efectiva contra el motor que escribe cada 1s. Renderiza `RentaVariableShell`.

Conecta con: backend `GET /api/scanner/cedears` y `/api/scanner/ccl` (service `scanner`); componente `RentaVariableShell`.

## Usa / conecta con →
- [[api.routers.scanner]]  ·  _module_
- [[web.cmp.renta-variable-shell]]  ·  _component_
- [[web.lib.api]]  ·  _lib_
- [[web.lib.types-scanner]]  ·  _lib_
