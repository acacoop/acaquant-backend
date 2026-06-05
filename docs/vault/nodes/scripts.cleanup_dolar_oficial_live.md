---
id: scripts.cleanup_dolar_oficial_live
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/cleanup_dolar_oficial_live.py
---

# scripts/cleanup_dolar_oficial_live

> cleanup_dolar_oficial_live.py — deja en Valuaciones.DolarOficialLive SOLO el dólar oficial.

**Archivo:** `scripts/cleanup_dolar_oficial_live.py`

## Qué hace
Limpieza one-shot que deja en Valuaciones.DolarOficialLive un único doc: el dólar oficial mayorista (UST$T/M/000). El feed MAE fue dejando combos huérfanos (CNH$T, MB$T, etc.) que ya no manda y ensucian la colección, aunque el sistema solo lee el oficial. Borra todo lo que no sea ese instrumento; muestra qué mantiene y qué borra antes de tocar. Se corre `python -m scripts.cleanup_dolar_oficial_live` (dry-run) y `--apply` para borrar.
Conecta con: Valuaciones.DolarOficialLive (borra), lectura via core.dolar_oficial.mid_oficial_live, core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.DolarOficialLive]]  ·  _collection_
