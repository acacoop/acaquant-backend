---
id: scripts.dump_openapi
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/dump_openapi.py
---

# scripts/dump_openapi

> scripts/dump_openapi.py — exporta los specs OpenAPI de las dos APIs.

**Archivo:** `scripts/dump_openapi.py`

## Qué hace
Herramienta reusable que exporta los specs OpenAPI de las dos APIs (la principal y la Partner API) a docs/postman/openapi_main.json y openapi_partner.json, forzando OpenAPI 3.0.3 para que Postman pueda importarlos. Hay que re-correrlo cada vez que se agregan o cambian endpoints y re-importar en Postman para mantener la colección en sync con el código. Se corre con `python -m scripts.dump_openapi`.
Conecta con: importa api.main y partner_api.main; genera la doc de Postman de ambas FastAPI.

## Usa / conecta con →
- [[api.main]]  ·  _module_
- [[partner_api.main]]  ·  _module_
