---
id: scripts.diag_scope_cuenta
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_scope_cuenta.py
---

# scripts/diag_scope_cuenta

> scripts/diag_scope_cuenta.py — READ-ONLY: formato de `cuenta` / `id_cuenta`.

**Archivo:** `scripts/diag_scope_cuenta.py`

## Qué hace
Diagnóstico read-only que inspecciona, por colección, el formato real del campo `cuenta` y la presencia de `id_cuenta` indexable. Existe para arreglar el scope de grupos sin romper control de acceso: _grupos_scope.py aplica un regex `cuenta: ^\[(id)\]` a todas las colecciones, pero en Operaciones `cuenta` es un id pelado ("805"), por lo que ese regex no matchearía y el scope quedaría roto ahí. Muestra muestras crudas y conteos (cuántos empiezan con '['). No escribe nada. Se corre con python -m scripts.diag_scope_cuenta.

Conecta con: colecciones con campo `cuenta`/`id_cuenta` (Operaciones, NegocioMovimientos, etc.), cliente Mongo de solo lectura. Valida api/services/_grupos_scope.py.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
