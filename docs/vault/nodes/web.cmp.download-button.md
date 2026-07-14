---
id: web.cmp.download-button
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\download-button.tsx
---

# web/components/download-button

**Archivo:** `src\components\download-button.tsx`

## Qué hace
Botón minimalista de descarga (típicamente XLSX) con icono download y estado de loading mientras se resuelve la promesa de export (la lib SheetJS es lazy-load). Reutilizable en headers de paneles.

Conecta con: utilitario de UI puro; recibe un callback `onClick` de export del componente que lo monta. No pega a la API.

## Lo usan (backlinks) ←
- [[web.cmp.aum-view]]  ·  _component_
- [[web.cmp.valuaciones-view]]  ·  _component_
