---
id: scripts.cleanup_assets_lowercase
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/cleanup_assets_lowercase.py
---

# scripts/cleanup_assets_lowercase

> cleanup_assets_lowercase.py — quita campos lowercase huérfanos de Valuaciones.Assets.

**Archivo:** `scripts/cleanup_assets_lowercase.py`

## Qué hace
Limpieza one-shot que borra (via $unset) las 7 keys lowercase huérfanas de Valuaciones.Assets, sobrantes de una migración vieja lowercase → UPPERCASE. La fuente de verdad hoy son las keys en MAYÚSCULAS que escriben jobs/aum.py y el PATCH de manager/assets; las lowercase son ruido que nadie lee ni actualiza. Riesgo cero, idempotente. Se corre `python -m scripts.cleanup_assets_lowercase` (dry-run) y `--apply` para ejecutar.
Conecta con: Valuaciones.Assets (escribe $unset), core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
