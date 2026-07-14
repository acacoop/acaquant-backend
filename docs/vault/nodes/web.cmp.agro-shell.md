---
id: web.cmp.agro-shell
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\agro-shell.tsx
---

# web/components/agro-shell

**Archivo:** `src\components\agro-shell.tsx`

## Qué hace
Contenedor con tabs del módulo Agro: "Mercado" (futuros + cadena de opciones + pizarra, vía `DerivadosAgroView`), "Mejoras Precio Dispo" y "Datos" (Cámara de Cereales). Recibe el snapshot inicial del mercado por SSR y arma la navegación entre sub-vistas. La pizarra abre a los 3 roles (`canEdit` siempre true).

Conecta con: monta `agro-view`/`derivados-agro-view`, `agro-mejoras-dispo` y `agro-datos`; recibe `agroInitial` (snapshot del backend) como prop.

## Usa / conecta con →
- [[web.lib.use-is-guest]]  ·  _lib_

## Lo usan (backlinks) ←
- [[web.view.agro.view]]  ·  _view_
