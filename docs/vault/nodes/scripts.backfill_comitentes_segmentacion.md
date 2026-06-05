---
id: scripts.backfill_comitentes_segmentacion
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_comitentes_segmentacion.py
---

# scripts/backfill_comitentes_segmentacion

> Backfill de los campos de segmentación MANUAL de Clientes.Comitentes desde CSV.

**Archivo:** `scripts/backfill_comitentes_segmentacion.py`

## Qué hace
Backfill desde CSV de los campos de segmentación MANUAL de `Clientes.Comitentes` (nivel_1..5, sucursal, referido, división, riesgo, etc.), que arrancan en null. Actualiza SOLO esos campos por `id_cuenta`, sin tocar los campos auto de Aunesa ni crear cuentas nuevas (la cuenta debe existir ya; correr `jobs.sync_comitentes` primero). Celdas vacías se ignoran; re-correr es idempotente.
Se corre con `python -m scripts.backfill_comitentes_segmentacion <csv> [--dry-run]`.
Conecta con: escribe `Clientes.Comitentes`; depende del master sincronizado por `jobs.sync_comitentes`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
