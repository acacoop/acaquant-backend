---
id: core.atlas_api
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/atlas_api.py
---

# core/atlas_api

> core/atlas_api.py — lector de la Atlas Admin API (REST de gestión).

**Archivo:** `core/atlas_api.py`

## Qué hace
Lector de la Atlas Admin API (REST de gestión de MongoDB en cloud.mongodb.com), separada del cluster: lee CPU por nodo y slow queries del M10 sin agregarle carga de queries a la DB. Autentica con HTTP Digest y prefiere la key read-only (`ATLAS_RO_*`) por mínimo privilegio.

Punto crítico de seguridad: los slow queries crudos pueden traer montos/nombres, así que `slow_queries_meta` los redacta a una whitelist de metadatos (ns, planSummary, docsExamined…) — es la única interfaz pública; el crudo nunca sale del módulo.

Conecta con: base del watchdog de DB y de informes de salud; pega a la Atlas Admin API REST; no toca el cluster Mongo.

## Lo usan (backlinks) ←
- [[jobs.watchdog]]  ·  _module_
