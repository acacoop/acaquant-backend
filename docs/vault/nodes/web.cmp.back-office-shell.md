---
id: web.cmp.back-office-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\back-office-shell.tsx
---

# web/components/back-office-shell

**Archivo:** `src\components\back-office-shell.tsx`

## Qué hace
Contenedor con tabs de la sección Back Office. Por ahora expone una sola pestaña, "Títulos / Mercado" (`TitulosMercadoView`); está armado para sumar futuras tabs (conciliación, archivos a enviar, etc.) sin reestructurar.

Conecta con: monta `titulos-mercado-view`, que consume los endpoints `/api/back-office` (service `back_office_titulos.py`).

## Usa / conecta con →
- [[web.lib.use-persisted-state]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.back-office.view]]  ·  _view_
