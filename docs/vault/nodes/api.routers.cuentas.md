---
id: api.routers.cuentas
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\cuentas.py
---

# api/routers/cuentas

> Router Cuentas: accionistas y contrapartes (ambos SQL — fuente única;

**Archivo:** `api\routers\cuentas.py`

## Qué hace
Router `/api/cuentas`: listados maestros de cuentas para selectores de la UI. `/accionistas` lee `CuentasAPI.AccionistasAPI`; `/contrapartes` lee directo de `CashFlow.Contrapartes` (nombre = contraparte, grupo = segmento), sin la copia intermedia. Ambos endpoints cacheados 1 hora.

Conecta con: lee `CuentasAPI.AccionistasAPI` (vía `api.db.get_db_cuentas`) y `CashFlow.Contrapartes` (vía `get_db_cashflow`); usa `api.cache.cached`; lo monta `api.main`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
