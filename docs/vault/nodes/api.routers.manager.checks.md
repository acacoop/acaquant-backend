---
id: api.routers.manager.checks
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/checks.py
---

# api/routers/manager/checks

> GET /api/manager/checks/* — validaciones de consistencia sobre Mongo.

**Archivo:** `api/routers/manager/checks.py`

## Qué hace
Sub-router `/api/manager/checks/*` — batería de validaciones de consistencia y debug paso-a-paso sobre los cálculos del sistema, para que la mesa diagnostique cuando un número se ve raro. Incluye: curvas pendientes de enriquecer, forwards (TEA por instrumento), CER usado en el último trade, tasa fija en AuM, debug-forward/soberano/curva-TEA, debug TNA de futuros DLR, debug de breakevens (Buscar Objetivo vs Fisher), pivot points, e introspección de instruments pyRofex por CFI. Solo lectura. Admin-only.

Conecta con: lee `Trading.Curvas/TimeSales/CER/DiasHabiles/FuturosDLRSnapshot/PreciosAcciones/ForwardsLive`, `Valuaciones.Assets/AuM`, `Manager.PyRofexDiscovery/PyRofexInstruments`; reusa funciones de `engines.curvas`, `engines.breakevens`, `quant.pivot_points` y los services `comercial`/`debug_curva`. Lo consume la tab CHECKS de la manager-view.

## Usa / conecta con →
- [[api.services.assets_sql]]  ·  _module_
- [[api.services.comercial_sql]]  ·  _module_
- [[api.services.debug_curva]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[engines.breakevens]]  ·  _module_
- [[engines.curvas]]  ·  _module_
- [[quant.pivot_points]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
- [[web.cmp.manager-debug-tea]]  ·  _component_
- [[web.cmp.manager-view]]  ·  _component_
