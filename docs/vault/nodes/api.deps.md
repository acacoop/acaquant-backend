---
id: api.deps
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\deps.py
---

# api/deps

> Dependencias de FastAPI (auth). Decomiso Mongo: el reexport de los viejos

**Archivo:** `api\deps.py`

## Qué hace
Dependencias de FastAPI más re-export de los helpers de DB. Aporta `verify_api_key` (valida el header `Authorization: Bearer <API_KEY>`; deja pasar todo si `API_KEY` no está seteada, modo dev) y re-exporta los `get_db_*` de `api/db.py` para que los routers viejos sigan importándolos desde acá.

Conecta con: lee `config.API_KEY`; re-exporta `api.db`; `verify_api_key` se monta global en `api.main`; la postura de auth la valida `api.main._validar_postura_auth`.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[api.routers.manager]]  ·  _module_
