---
id: web.lib.me
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src\lib\me.ts
---

# web/lib/me

**Archivo:** `src\lib\me.ts`

## Qué hace
Lee `GET /api/me` del backend para obtener la identidad y permisos del usuario actual (email, rol, módulos habilitados, flag admin). Propaga el email de confianza de Cloudflare Access + el auth del frontend (Bearer + CF service token). Envuelto en `React.cache()` para deduplicar el round-trip cuando layout y page lo llaman en el mismo render. Devuelve `null` si el fetch falla (el caller decide: en dev mostrar todo, en prod ocultar links de admin).

Conecta con: resuelve la identidad con `web.lib.cf-access` (`trustedEmail`); le pega al endpoint `/api/me` (`api.routers.me`); su salida gobierna qué links/módulos renderiza el shell del frontend según RBAC.

## Lo usan (backlinks) ←
- [[web.view.(home).layout]]  ·  _view_
- [[web.view.derivados.view]]  ·  _view_
- [[web.view.manager.view]]  ·  _view_
