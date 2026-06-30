---
id: api.routers.titulos
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/titulos.py
---

# api/routers/titulos

> Router Titulos: assets + flujos, DIRECTO desde las fuentes (Valuaciones.Assets

**Archivo:** `api/routers/titulos.py`

## Qué hace
Router de consulta del maestro de títulos. `GET /api/titulos/assets` lista activos (unidad, ticker, emisor, cartera, clase, calificación, vencimiento) con filtros simples, y otros endpoints sirven flujos. Cacheado (TTL 600s) porque el maestro cambia poco.

Conecta con: lee las colecciones derivadas `AssetsAPI`/`FlujosAPI` (vía `get_db_titulos`); usa helper `_bonos_cer_fijados` de renta_fija; lo consume el frontend para selectores y fichas de instrumentos.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.titulos_flujos]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.lib.proxy]]  ·  _lib_
- [[web.view.renta-fija.view]]  ·  _view_
