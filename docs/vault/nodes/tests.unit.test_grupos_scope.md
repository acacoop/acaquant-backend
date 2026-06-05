---
id: tests.unit.test_grupos_scope
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_grupos_scope.py
---

# tests/unit/test_grupos_scope

> Golden tests del scope de cuentas por grupo (api/services/_grupos_scope.py).

**Archivo:** `tests/unit/test_grupos_scope.py`

## Qué hace
Golden tests del scoping de cuentas por grupo (`api/services/_grupos_scope.py`): un usuario scopeado no puede tocar cuentas ajenas. Cubre la construcción del match Mongo ($in indexable por `cuenta`/`id_cuenta`, regex bracketed por defecto), el guard de los endpoints de órdenes (`verificar_account` → 403 fuera de scope, 400 si scopeado no especifica cuenta) y el filtrado de listas. `scope=None` (admin/sin grupo) pasa sin restricción.

Conecta con: importa `api.services._grupos_scope`; red de seguridad del enforcement multi-tenant que protege órdenes, operativa y brackets (fix C1 2026-05-23).

## Usa / conecta con →
- [[api.services._grupos_scope]]  ·  _module_
