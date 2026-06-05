---
id: scripts.clear_cupo_cuenta
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/clear_cupo_cuenta.py
---

# scripts/clear_cupo_cuenta

> Borra el `cupo` (subdoc completo) de UNA cuenta — para corregir una carga

**Archivo:** `scripts/clear_cupo_cuenta.py`

## Qué hace
Utilidad puntual para borrar el subdoc `cupo` completo de UNA cuenta de Clientes.Comitentes, cuando hubo una carga manual mala; la deja como "sin cupo cargado" y la segmentación patrimonial la trata como tal. Dry-run por default (muestra el cupo actual), `--apply` lo borra con $unset. Se corre `python -m scripts.clear_cupo_cuenta --id 805 [--apply]`.
Conecta con: Clientes.Comitentes (escribe $unset cupo), insumo de la segmentación patrimonial, core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
