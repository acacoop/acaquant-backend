---
id: scripts.watch_db
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/watch_db.py
---

# scripts/watch_db

> scripts/watch_db.py — feed EN VIVO de todo lo que se ESCRIBE en Mongo.

**Archivo:** `scripts/watch_db.py`

## Qué hace
Herramienta reusable de diagnóstico: feed en vivo de TODO lo que se escribe en Mongo. Abre un change stream del cluster (cursor tailable sobre el oplog que Mongo ya escribe, sin agregar carga de lectura) e imprime cada insert/update/replace/delete con el namespace, tipo de op y campos clave. Evita a propósito full_document=updateLookup para no fetchear el doc por cada update. Filtrable por --db, --col y --op. Se deja corriendo en una terminal del Droplet. Uso: `python -m scripts.watch_db [--db X] [--col Y] [--op insert,update]`.

Conecta con: change stream del cluster Mongo (oplog) vía core.mongo; herramienta de observabilidad, no escribe nada.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
