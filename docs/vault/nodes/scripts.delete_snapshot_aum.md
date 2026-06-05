---
id: scripts.delete_snapshot_aum
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/delete_snapshot_aum.py
---

# scripts/delete_snapshot_aum

> delete_snapshot_aum.py — borra un snapshot completo de Valuaciones.AuM.

**Archivo:** `scripts/delete_snapshot_aum.py`

## Qué hace
Utilidad puntual para borrar un snapshot diario completo de Valuaciones.AuM cuando corrió incompleto y hay que rehacerlo desde cero (borrar + re-correr jobs.aum_backfill para esa fecha). Dry-run por default (solo cuenta los docs del snapshot), `--apply` borra. Se corre `python -m scripts.delete_snapshot_aum --snapshot 2026-05-05 [--apply]`.
Conecta con: Valuaciones.AuM (borra por fecha_snapshot), se rehace con jobs.aum_backfill, core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.AuM]]  ·  _collection_
