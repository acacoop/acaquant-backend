---
id: core.snapshot_writer
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/snapshot_writer.py
---

# core/snapshot_writer

**Archivo:** `core/snapshot_writer.py`

## Qué hace
Escritor en background genérico para cualquier motor de mercado. En un thread daemon llama a una `data_fn()` cada ~0.5s, hashea el resultado (MD5) y solo escribe a Mongo si el estado cambió; cuando cambia hace un `bulk_write` de upserts (un round-trip sin importar cuántos activos). Reconecta solo si el driver falla. Es el patrón estándar para persistir snapshots live sin martillar Atlas.

Conecta con: escribe a la colección/DB que le pasa cada motor (ej. `Trading.MarketSnapshot`, `Trading.FuturosDLRSnapshot`) vía `core.mongo`. Lo instancian los `engines/*` pasándole su `get_snapshot_data` y `key_field`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[core.threads]]  ·  _module_
