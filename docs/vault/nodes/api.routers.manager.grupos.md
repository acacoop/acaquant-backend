---
id: api.routers.manager.grupos
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/grupos.py
---

# api/routers/manager/grupos

> Manager sub-router — grupos de acceso por cuenta.

**Archivo:** `api/routers/manager/grupos.py`

## Qué hace
Sub-router `/api/manager/grupos` — CRUD de grupos de acceso por cuenta (scoping multi-tenant). GET devuelve los grupos existentes más las cuentas reales (del último snapshot AuM) para armar el selector; POST/PATCH/DELETE crean, editan y eliminan grupos. Borrar un grupo devuelve a sus usuarios a ver TODO (sin grupo = sin restricción). Admin-only.

Conecta con: `core.grupos` (crear/actualizar/eliminar/listar, persiste en Mongo) y `api.services.portfolio::listar_cuentas` para el selector. El enforcement real del scope vive en `api.services._grupos_scope`. Lo consume la tab GRUPOS de la manager-view. Ver project_grupos.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services.portfolio_sql]]  ·  _module_
- [[core.grupos]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
- [[web.cmp.grupos-panel]]  ·  _component_
