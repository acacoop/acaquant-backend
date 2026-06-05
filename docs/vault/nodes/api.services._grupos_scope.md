---
id: api.services._grupos_scope
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/_grupos_scope.py
---

# api/services/_grupos_scope

> Enforcement de grupos — scoping de cuentas por usuario (Fase 2).

**Archivo:** `api/services/_grupos_scope.py`

## Qué hace
Cara "router" del feature de grupos: aplica en la capa HTTP el scoping de cuentas por usuario. Provee dependencies FastAPI (`scope_cuentas` inyecta el tuple de id_cuenta visibles o `None` sin restricción; `verificar_id_cuenta`/`verificar_account` tiran 403 si la cuenta queda fuera) y helpers para filtrar listas ya materializadas. `None` = sin restricción (admin); tuple vacío = no ve nada.

Conecta con: resuelve las cuentas con `core.grupos.cuentas_visibles` (lee la config de grupos en Mongo); lo importan los routers de ordenes, operar, risk, valuaciones, operaciones; los services reciben el scope como parámetro hashable (cache key).

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[core.grupos]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[tests.unit.test_grupos_scope]]  ·  _module_
