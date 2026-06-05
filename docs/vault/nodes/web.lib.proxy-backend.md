---
id: web.lib.proxy-backend
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/lib/proxy-backend.ts
---

# web/lib/proxy-backend

**Archivo:** `src/lib/proxy-backend.ts`

## Qué hace
Helper de proxy transparente hacia el backend, usado por los route handlers de Next que necesitan conservar la semántica HTTP del backend. A diferencia de `apiFetch`, propaga el status code y el body tal cual (un 404 del backend llega como 404, no como 500 genérico) — necesario para CRUD. Reenvía Bearer + CF service token + identidad del usuario; soporta GET/POST/PUT/PATCH/DELETE con body crudo y maneja el 204 sin cuerpo.

Conecta con: resuelve la identidad con `web.lib.cf-access` (`trustedEmail`); reenvía requests a cualquier path del backend (`api.main`); lo invocan los route handlers `route.ts` del frontend que envuelven endpoints de escritura/CRUD.

_Sin conexiones detectadas mecánicamente._
