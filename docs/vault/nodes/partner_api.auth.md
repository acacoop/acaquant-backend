---
id: partner_api.auth
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api/auth.py
---

# partner_api/auth

> Auth del partner_api — login usuario/password → JWT, y el guard de los

**Archivo:** `partner_api/auth.py`

## Qué hace
Maneja login y el guard de los endpoints de datos. `POST /v1/token` recibe usuario+password, lo verifica contra `ApiUsers` (con hash dummy si el usuario no existe, para no filtrar por timing) y devuelve un JWT de vida corta; rate-limit agresivo (10/min) contra fuerza bruta. La dependency `usuario_actual` valida el Bearer y re-chequea en la DB que el proveedor siga habilitado, así deshabilitarlo lo deja afuera al instante.

Conecta con: lee `ACAPortfolio.ApiUsers` vía `partner_api.db.get_db`; usa `crear_token`/`validar_token`/`verify_password` de `partner_api.security` y el `limiter`/`client_ip` de `partner_api.ratelimit`. Los usuarios se crean con `scripts/partner_user.py`. Lo consume `partner_api.routes` (que depende de `usuario_actual`).

## Usa / conecta con →
- [[partner_api]]  ·  _module_
- [[partner_api.ratelimit]]  ·  _module_
- [[partner_api.security]]  ·  _module_
- [[partner_api.store]]  ·  _module_

## Lo usan (backlinks) ←
- [[partner_api.main]]  ·  _module_
- [[partner_api.odata]]  ·  _module_
- [[partner_api.routes]]  ·  _module_
