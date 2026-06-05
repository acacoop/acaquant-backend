---
id: db.CuentasAPI.AccionistasAPI
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CuentasAPI.AccionistasAPI

> Colección Mongo en DB CuentasAPI.

## Qué hace
Copia derivada (read-friendly para la API) del maestro de accionistas/titulares, en la base `CuentasAPI`. Se regenera a partir de la fuente para servir consultas de la API sin tocar la colección original.

Conecta con: la re-sincroniza `jobs/sync_api_copies.py` / `scripts.api_migrate`; la leen `api/routers/cuentas.py`, `api/services/risk.py` y `_cuentas_filter.py`.

## Lo usan (backlinks) ←
- [[api.routers.cuentas]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[scripts.api_migrate]]  ·  _module_
- [[scripts.gen_obsidian]]  ·  _module_
