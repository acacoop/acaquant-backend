---
id: partner_api.security
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api\security.py
---

# partner_api/security

> Hashing de passwords + emisión/validación de JWT para partner_api.

**Archivo:** `partner_api\security.py`

## Qué hace
Primitivas de seguridad: hashing de passwords y emisión/validación de JWT. Los passwords se hashean con PBKDF2-HMAC-SHA256 (600k iteraciones, stdlib) y se guardan como `salt_hex$hash_hex`; nunca se persiste el texto plano. Los tokens son JWT HS256 firmados con `PARTNER_JWT_SECRET`, de vida corta (`PARTNER_TOKEN_TTL_MIN`, default 60 min). La verificación de password es timing-safe.

Conecta con: lee `PARTNER_JWT_SECRET` y `PARTNER_TOKEN_TTL_MIN` de `partner_api.settings`. Lo usan `partner_api.auth` (login y guard) y `partner_api.main` (middleware de auditoría que decodea el token para loguear el usuario).

## Usa / conecta con →
- [[partner_api.settings]]  ·  _module_

## Lo usan (backlinks) ←
- [[partner_api.auth]]  ·  _module_
- [[partner_api.main]]  ·  _module_
