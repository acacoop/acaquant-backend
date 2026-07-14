---
id: api.routers.scanner
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\scanner.py
---

# api/routers/scanner

> Router /api/scanner — vista Scanner del módulo Renta Variable.

**Archivo:** `api\routers\scanner.py`

## Qué hace
Router de la vista Scanner del módulo Renta Variable. `GET /api/scanner/cedears` devuelve la lista de CEDEARs activos cruzando el master con el snapshot live (underlying, ratio, sector, last/open/high/low, intraday% y vs-1D en ARS/USD); `/ccl` da el CCL live + variación 1D para el KPI del shell.

Conecta con: delega en `api.services.scanner` y `api.services.rv_motor` (leen `Trading.CedearsSnapshot` + `Trading.PreciosAcciones`, alimentadas por `engines.motor_cedears`); gate RBAC módulo `renta-variable`; lo consume /renta-variable del frontend.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.day_trading]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.api.api.scanner.[...path]]]  ·  _route_
- [[web.cmp.pivot-points-panel]]  ·  _component_
- [[web.cmp.scanner-view]]  ·  _component_
- [[web.cmp.ticker-chart-panel]]  ·  _component_
- [[web.cmp.trading-movers-scanner]]  ·  _component_
- [[web.cmp.trading-view]]  ·  _component_
- [[web.cmp.trading-volumen-scanner]]  ·  _component_
- [[web.lib.proxy]]  ·  _lib_
- [[web.view.renta-variable.view]]  ·  _view_
