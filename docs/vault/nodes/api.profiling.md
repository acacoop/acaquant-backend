---
id: api.profiling
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/profiling.py
---

# api/profiling

> api/profiling.py — Middleware opt-in de profiling de requests (pyinstrument).

**Archivo:** `api/profiling.py`

## Qué hace
Middleware opt-in de profiling de requests con pyinstrument. Solo se monta si `config.API_PROFILING` está prendido; entonces cualquier request con `?profile=1` se corre bajo un profiler estadístico y devuelve el árbol de llamadas (HTML) o JSON para speedscope (`?profile=speedscope`), en vez de la respuesta normal. Sin el query param el request pasa derecho (overhead nulo). Sirve para distinguir CPU propio vs espera de I/O de Mongo.

Conecta con: lo monta `api.main` (vía `maybe_add_profiler`) solo con el flag activo; importa pyinstrument de forma perezosa.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
