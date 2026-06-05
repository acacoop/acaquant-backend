---
id: scripts.drop_indice_serie_aranceles
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/drop_indice_serie_aranceles.py
---

# scripts/drop_indice_serie_aranceles

> Borra el índice serie_aranceles_cov — NO sirvió (medido).

**Archivo:** `scripts/drop_indice_serie_aranceles.py`

## Qué hace
One-shot de mantenimiento que borra el índice `serie_aranceles_cov` de CashFlow.Operaciones. Se había creado para cubrir la serie de /ops/aranceles, pero el explain mostró que el $group seguía haciendo FETCH por documento (no cubría) y no mejoraba el wall-clock, así que se elimina para no gastar RAM/disco en un índice grande inútil. Se corre con `python -m scripts.drop_indice_serie_aranceles`.
Conecta con: opera sobre CashFlow.Operaciones; revierte un intento de optimización del endpoint /ops/aranceles.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
