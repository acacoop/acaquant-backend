---
id: api.routers.titulos
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\titulos.py
---

# api/routers/titulos

> Router Titulos: endpoints para AssetsAPI y FlujosAPI.

**Archivo:** `api\routers\titulos.py`

## Qué hace
Router de consulta del maestro de títulos. `GET /api/titulos/assets` lista activos (unidad, ticker, emisor, cartera, clase, calificación, vencimiento) con filtros simples, y otros endpoints sirven flujos. Cacheado (TTL 600s) porque el maestro cambia poco.

Conecta con: lee las colecciones derivadas `AssetsAPI`/`FlujosAPI` (vía `get_db_titulos`); usa helper `_bonos_cer_fijados` de renta_fija; lo consume el frontend para selectores y fichas de instrumentos.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.deps]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[db.Trading.Curvas]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.lib.proxy]]  ·  _lib_
- [[web.view.renta-fija.view]]  ·  _view_
