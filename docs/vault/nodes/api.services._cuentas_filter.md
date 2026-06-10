---
id: api.services._cuentas_filter
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/_cuentas_filter.py
---

# api/services/_cuentas_filter

> Helpers compartidos para filtrar pipelines Mongo por tipo de cuenta.

**Archivo:** `api/services/_cuentas_filter.py`

## Qué hace
Helper compartido que traduce un filtro de tipo de cuenta en un sub-doc `$match` de Mongo, para que el comportamiento sea idéntico en todas las vistas. Soporta `todas`, `accionistas` (∈ AccionistasAPI), `sin_accionistas`, `cooperativas` (no accionista + nombre con "coop") y `productores` (id_cuenta de Comitentes con nivel_1=PRODUCTORES). `VALID_FILTERS` es la fuente única para sumar tipos nuevos.

Conecta con: lee `CuentasAPI.AccionistasAPI` y `Clientes.Comitentes` (con `@cached` TTL 600); lo usan los services/routers de Operaciones y Portfolio para filtrar pipelines por cartera/AuM.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.operaciones]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
