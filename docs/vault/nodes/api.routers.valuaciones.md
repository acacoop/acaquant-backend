---
id: api.routers.valuaciones
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/valuaciones.py
---

# api/routers/valuaciones

> Router /api/valuaciones — performance e historia por cuenta.

**Archivo:** `api/routers/valuaciones.py`

## Qué hace
Router de performance e historia por cuenta. `GET /consolidado` da una fila por cuenta (valor, base-100, PnL acum, TEM, TEA en ARS y USD) para comparar carteras; `/{id}/serie` la curva diaria del portfolio; `/{id}/mensual` el cierre mensual con flujos externos; `/{id}/posiciones` el ledger cost-basis (PnL realizado/no-realizado, drill-down per-ticker).

Conecta con: delega en `api.services.valuaciones` (lee `Valuaciones.AuM`, `ConsolidadoCuentas`, `NegocioMovimientos`); scope de grupos por cuenta (`verificar_id_cuenta`/`scope_cuentas`); lo consume la vista /aum del frontend.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.api.api.valuaciones.[id_cuenta].mensual]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].movimientos]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].posiciones]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].posiciones-actuales]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].serie]]  ·  _route_
- [[web.api.api.valuaciones.[id_cuenta].variacion]]  ·  _route_
- [[web.api.api.valuaciones.consolidado]]  ·  _route_
