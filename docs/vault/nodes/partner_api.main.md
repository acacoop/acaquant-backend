---
id: partner_api.main
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api\main.py
---

# partner_api/main

> partner_api — app FastAPI del servicio externo de datos para el proveedor.

**Archivo:** `partner_api\main.py`

## Qué hace
Entrypoint FastAPI del servicio externo. Monta los routers de auth y de datos, valida al arrancar que estén `PARTNER_MONGO_URI` y `PARTNER_JWT_SECRET` (si faltan, loguea error fuerte), y desactiva Swagger/OpenAPI público para no exponer el esquema. Agrega un middleware de auditoría que loguea cada request (IP, usuario, método, path, status, latencia) y expone `GET /health` sin auth.

Conecta con: incluye `partner_api.auth` (`/v1/token`) y `partner_api.routes` (`/v1/fechas`, `/v1/portfolio`); usa el `limiter` de `partner_api.ratelimit` y `validar_token` de `partner_api.security`. Se arranca con `uvicorn partner_api.main:app --port 8100`; en prod lo corre `partner_api.service`.

## Usa / conecta con →
- [[partner_api]]  ·  _module_
- [[partner_api.auth]]  ·  _module_
- [[partner_api.odata]]  ·  _module_
- [[partner_api.pg]]  ·  _module_
- [[partner_api.ratelimit]]  ·  _module_
- [[partner_api.routes]]  ·  _module_
- [[partner_api.security]]  ·  _module_
- [[partner_api.settings]]  ·  _module_
