---
id: api.auth
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\auth.py
---

# api/auth

> Autenticación de identidad — validación JWT de Cloudflare Access.

**Archivo:** `api\auth.py`

## Qué hace
Autenticación de identidad de la API: valida criptográficamente el JWT que emite Cloudflare Access (en vez de confiar en el header de email, que es spoofable). Expone los dependencies de FastAPI `get_user_email` (identidad del caller), `require_manager` (gate de admin por `MANAGER_EMAILS`) y `require_module`/`require_any_module` (RBAC por módulo). Fail-open controlado en dev si faltan `CF_ACCESS_TEAM`/`CF_ACCESS_AUD`.

Conecta con: lee las claves públicas JWKS de Cloudflare; lo usan casi todos los routers como `Depends(...)`; la matriz de permisos vive en `core.roles`; el OAuth del MCP (`api.mcp.oauth`) reusa `get_user_email`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.roles]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[api.mcp.oauth]]  ·  _module_
- [[api.routers.back_office]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.derivados_agro]]  ·  _module_
- [[api.routers.derivados_sinteticos]]  ·  _module_
- [[api.routers.ia]]  ·  _module_
- [[api.routers.manager]]  ·  _module_
- [[api.routers.manager.aca_valores]]  ·  _module_
- [[api.routers.manager.assets]]  ·  _module_
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.routers.manager.bonos]]  ·  _module_
- [[api.routers.manager.breakevens]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.contrapartes]]  ·  _module_
- [[api.routers.manager.control_automatico]]  ·  _module_
- [[api.routers.manager.grupos]]  ·  _module_
- [[api.routers.manager.import_tenencia]]  ·  _module_
- [[api.routers.manager.ons]]  ·  _module_
- [[api.routers.manager.roles]]  ·  _module_
- [[api.routers.manager.users]]  ·  _module_
- [[api.routers.me]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.research]]  ·  _module_
- [[api.routers.research1816]]  ·  _module_
- [[api.routers.research_bcra]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.telemetria]]  ·  _module_
