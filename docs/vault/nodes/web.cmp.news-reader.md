---
id: web.cmp.news-reader
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src/components/news-reader.tsx
---

# web/components/news-reader

**Archivo:** `src/components/news-reader.tsx`

## Qué hace
Modo lectura de un artículo de noticias: dado un URL, trae el texto limpio (reader mode) y lo muestra en un panel con título, autor, fecha y hostname, con fallbacks si la extracción falla.

Conecta con: fetch a `/api/news/article?url=X` (extracción reader-mode del backend, router `news`). Lo abre `news-panel` al clickear un titular.

_Sin conexiones detectadas mecánicamente._
