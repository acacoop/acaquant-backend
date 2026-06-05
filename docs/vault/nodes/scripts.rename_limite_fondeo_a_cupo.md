---
id: scripts.rename_limite_fondeo_a_cupo
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/rename_limite_fondeo_a_cupo.py
---

# scripts/rename_limite_fondeo_a_cupo

> rename_limite_fondeo_a_cupo.py — migración del rename del subdoc en Clientes.Comitentes.

**Archivo:** `scripts/rename_limite_fondeo_a_cupo.py`

## Qué hace
Migración one-shot que renombra el subdoc `limite_fondeo` → `cupo` en Clientes.Comitentes (y dentro: disponible_ars → transaccional_ars, utilizado_ars → usado_ars), dejando el resto de los campos del subdoc intactos. Usa $rename de Mongo, que solo opera sobre docs con el campo origen, así que es idempotente y seguro de re-correr. Por defecto dry-run (cuenta afectados); con --apply ejecuta. Uso: `python -m scripts.rename_limite_fondeo_a_cupo [--apply]`.

Conecta con: Clientes.Comitentes (escribe), core.mongo (rw); base de la feature de segmentación patrimonial (api.services.segmentacion lee `cupo`).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
