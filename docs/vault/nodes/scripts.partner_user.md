---
id: scripts.partner_user
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/partner_user.py
---

# scripts/partner_user

> scripts/partner_user.py — gestión de usuarios de la Partner API.

**Archivo:** `scripts/partner_user.py`

## Qué hace
Herramienta reusable de gestión de usuarios de la Partner API: crear, resetear password, habilitar/deshabilitar y listar. Los usuarios viven en ACAPortfolio.ApiUsers y los consume partner_api/auth.py; este script usa el Mongo rw de la mesa porque el servicio partner_api es read-only y no puede crear usuarios. crear/reset generan un password random que se imprime UNA sola vez (en la DB solo va el hash). Se corre con `python -m scripts.partner_user crear|reset|habilitar|deshabilitar|listar <username>`.

Conecta con: ACAPortfolio.ApiUsers, partner_api.security (hash_password), partner_api.auth (lo consume), core.mongo (rw).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[partner_api.security]]  ·  _module_
