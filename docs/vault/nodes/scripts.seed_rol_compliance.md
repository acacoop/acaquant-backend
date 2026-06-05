---
id: scripts.seed_rol_compliance
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/seed_rol_compliance.py
---

# scripts/seed_rol_compliance

> scripts/seed_rol_compliance.py — crea el rol `compliance` en Manager.RoleMatrix.

**Archivo:** `scripts/seed_rol_compliance.py`

## Qué hace
Seed idempotente que crea el rol `compliance` en Manager.RoleMatrix (prod usa la matriz de la DB, que pisa el DEFAULT_MATRIX del código, así que un rol nuevo no aparece en /manager → ROLES hasta sembrarlo). Le asigna HOME + todos los mercados + Manager solo Clientes + Compliance, sin el umbrella `manager` (no ve tabs admin). Chequea defensivamente que todos los módulos sean canónicos antes de escribir. Tras el run el rol aparece en la UI y se puede asignar a usuarios. Uso: `python -m scripts.seed_rol_compliance [--dry-run]`.

Conecta con: core.roles (MODULES, get_matrix, set_role_modules), Manager.RoleMatrix, Manager.RoleAudit.

## Usa / conecta con →
- [[core.roles]]  ·  _module_
