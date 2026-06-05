---
id: web.api.api.news.article
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src/app/api/news/article/route.ts
---

# web /api/news/article  (proxy)

**Archivo:** `src/app/api/news/article/route.ts`

## Qué hace
Route handler que proxea el reader-mode de un artículo: recibe ?url, valida y pega a /api/news/article del backend para traer el texto limpio. maxDuration 30s por el scraping, sin cache.
- Conecta con: backend /api/news/article (api.routers.news); usado al abrir una noticia en modo lectura.

## Usa / conecta con →
- [[api.routers.news]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
