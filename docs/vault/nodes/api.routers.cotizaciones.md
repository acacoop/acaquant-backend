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
- [[api.services.curvas_vista]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.mercado_hist_sql]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.opciones_sql]]  ·  _module_
- [[api.services.rem_sql]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
