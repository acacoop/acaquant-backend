---
id: scripts.audit_assets_instrumento
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/audit_assets_instrumento.py
---

# scripts/audit_assets_instrumento

> audit_assets_instrumento.py — read-only.

**Archivo:** `scripts/audit_assets_instrumento.py`

## Qué hace
Diagnóstico read-only que audita el campo `INSTRUMENTO` de `Valuaciones.Assets` cruzándolo contra la lista canónica de instrumentos primary BYMA 24hs (`Manager.PyRofexInstruments`). Reporta cuántos Assets tienen INSTRUMENTO seteado, cuántos matchean un symbol real con feed y cuántos son strings ciegos sin feed (sugiriendo plazos alternativos). No modifica nada.
Se corre con `python -m scripts.audit_assets_instrumento [--top 30]`.
Conecta con: lee `Valuaciones.Assets` y `Manager.PyRofexInstruments`; usa `core.mongo`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
