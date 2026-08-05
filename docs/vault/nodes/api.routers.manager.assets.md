---
id: api.routers.manager.assets
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/assets.py
---

# api/routers/manager/assets

> Manager sub-router — control del catálogo de títulos (segmentación).

**Archivo:** `api/routers/manager/assets.py`

## Qué hace
Sub-router `/api/manager/assets` — tab ASSETS del Manager para auditar y completar metadatos faltantes (CARTERA, EMISOR, INSTRUMENTO, CLASE_ACTIVO, etc.) en `Valuaciones.Assets`, que es la fuente de verdad UPPERCASE de la que se derivan el resto de catálogos. Lista con filtros (default = solo gaps), expone valores únicos para autocomplete y permite editar fila a fila (PATCH con `unidad` en el body para evitar problemas de URL-encoding). Admin-only.

Conecta con: lee/escribe `Valuaciones.Assets` (read client para GET, rw para PATCH); el campo CAFCI es read-only (lo deriva jobs/aum). Aguas abajo, `scripts/api_migrate.py:assets` deriva `TitulosAPI.AssetsAPI`. Auth `get_user_email` para el audit liviano.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.assets_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
