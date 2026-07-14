---
id: web.api.api.news
type: route
layer: web-api
repo: frontend
tags: [route, web-api, frontend]
path: src\app\api\news\route.ts
---

# web /api/news  (proxy)

**Archivo:** `src\app\api\news\route.ts`

## Qué hace
Route handler que proxea los titulares de noticias al backend (/api/news), reenviando el querystring. Sin cache.
- Conecta con: backend /api/news (api.routers.news, lee News.Headlines); consumido por la vista de noticias del frontend.

## Usa / conecta con →
- [[api.routers.news]]  ·  _module_
- [[web.lib.api]]  ·  _lib_
