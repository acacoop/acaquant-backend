---
id: web.cmp.derivados-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/derivados-shell.tsx
---

# web/components/derivados-shell

**Archivo:** `src/components/derivados-shell.tsx`

## Qué hace
Shell delgado del módulo Derivados que hoy solo muestra Opciones GGAL (Agro y Sintéticos se mudaron a `/agro` y `/sinteticos` top-level). Recibe los docs de la chain y la meta de opciones desde la page server-side y los pasa a la vista.

Conecta con: renderiza `web.cmp.derivados-view`; la page que lo monta hidrata los datos iniciales desde el backend de opciones (service `api.services.opciones`).

## Usa / conecta con →
- [[web.lib.estrategias]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.derivados.view]]  ·  _view_
