---
id: scripts.seed_asistente_comercial
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/seed_asistente_comercial.py
---

# scripts/seed_asistente_comercial

> seed_asistente_comercial.py — alta del rol `asistente_comercial` + sub-módulos de Manager.

**Archivo:** `scripts/seed_asistente_comercial.py`

## Qué hace
Seed idempotente de RBAC: da de alta el rol `asistente_comercial` en Manager.RoleMatrix con la lista canónica de core.roles.DEFAULT_MATRIX, y suma los sub-módulos nuevos (manager_comercial, manager_clientes, manager_clientes_bulk) al rol admin sin pisar lo que ya tiene. Cada cambio queda en Manager.RoleAudit con actor del script. El cache RBAC tiene TTL 60s, así que toma efecto sin restart en ≤1 minuto. Por defecto --dry-run; con --apply escribe. Uso: `python -m scripts.seed_asistente_comercial [--apply]`.

Conecta con: core.roles (DEFAULT_MATRIX/MODULES, set_role_modules), Manager.RoleMatrix, Manager.RoleAudit.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.roles]]  ·  _module_
- [[db.Manager.RoleAudit]]  ·  _collection_
- [[db.Manager.RoleMatrix]]  ·  _collection_
