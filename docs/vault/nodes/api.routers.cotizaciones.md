---
id: api.routers.cotizaciones
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/cotizaciones.py
---

# api/routers/cotizaciones

> Router Cotizaciones — thin wrappers sobre la capa de servicio.

**Archivo:** `api/routers/cotizaciones.py`

## Qué hace
Router `/api/cotizaciones`: thin wrappers sobre la capa de servicio para cotizaciones de mercado, organizados por dominio — macro (series BCRA BADLAR/CER/DOLAR + dólar MEP), repo/caución, derivados (futuros DLR, forwards, breakevens), renta fija, opciones, fair value, REM y el panel `argy` con returns calculados.

Conecta con: delega en services `macro`, `repo`, `derivados`, `renta_fija`, `opciones`, `fair_value`, `rem`, `argy`; aplica RBAC vía `api.auth.require_module`; lo monta `api.main`.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.rem]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.api.api.argy]]  ·  _route_
- [[web.api.api.caucion]]  ·  _route_
- [[web.api.api.dolares-historico]]  ·  _route_
- [[web.api.api.futuros-dlr]]  ·  _route_
- [[web.api.api.historico-curva]]  ·  _route_
- [[web.api.api.mep]]  ·  _route_
- [[web.api.api.opciones-meta]]  ·  _route_
- [[web.api.api.trades]]  ·  _route_
- [[web.cmp.breakevens-block]]  ·  _component_
- [[web.cmp.costo-historico-chart]]  ·  _component_
- [[web.cmp.derivados-view]]  ·  _component_
- [[web.cmp.fair-value-modal]]  ·  _component_
- [[web.cmp.fair-value-view]]  ·  _component_
- [[web.cmp.forwards-panel]]  ·  _component_
- [[web.cmp.griegas-historico-chart]]  ·  _component_
- [[web.cmp.opcion-historico-chart]]  ·  _component_
- [[web.cmp.renta-fija-live]]  ·  _component_
- [[web.view.derivados.view]]  ·  _view_
- [[web.view.renta-fija.view]]  ·  _view_
