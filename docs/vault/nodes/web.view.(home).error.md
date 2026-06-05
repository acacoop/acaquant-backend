---
id: web.view.(home).error
type: view
layer: web-view
repo: frontend
tags: [view, web-view, frontend]
path: src/app/error.tsx
---

# web /(home)  (error)

**Archivo:** `src/app/error.tsx`

## Qué hace
Error boundary global del frontend. Next 15 lo monta automáticamente en cualquier route group que no tenga su propio `error.tsx`, así un componente que rompe no deja toda la página en blanco. Muestra el mensaje de error (y el `digest` de Next si lo hay) en un panel mono, con botón "Reintentar" (llama a `reset()` de Next para re-renderizar) y un link "Ir al inicio". El texto sugiere revisar logs de la API o del motor correspondiente cuando el fallo persiste.

Conecta con: es un client component puro de Next.js (no consume API ni colecciones); usa las CSS vars del tema (`--t-*`); lo invoca el runtime de Next al capturar una excepción durante el render de cualquier vista.

_Sin conexiones detectadas mecánicamente._
