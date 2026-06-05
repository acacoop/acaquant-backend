---
id: web.view.back-office.view
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src\app\back-office\page.tsx
---

# web /back-office  (view)

**Archivo:** `src\app\back-office\page.tsx`

## Qué hace
Vista `/back-office` — sección Back Office. Hoy tiene una sola sub-tab (Títulos/Mercado) pero el shell deja lugar para más. Wrapper `force-dynamic` que renderiza `BackOfficeShell`.

Conecta con: componente `BackOfficeShell` → backend `/api/back-office` (service `back_office_titulos`).

## Usa / conecta con →
- [[web.cmp.back-office-shell]]  ·  _component_
