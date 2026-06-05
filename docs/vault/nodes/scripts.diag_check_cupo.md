---
id: scripts.diag_check_cupo
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_check_cupo.py
---

# scripts/diag_check_cupo

> Diag READ-ONLY: verifica que el `cupo` de las Comitentes está intacto.

**Archivo:** `scripts/diag_check_cupo.py`

## Qué hace
Diagnóstico read-only que verifica que el campo `cupo` (límite de fondeo del custodio) de las Comitentes sigue intacto. Cuenta cuántas cuentas tienen cupo cargado y usado, y muestra ejemplos con denominacion, nivel_3 y los montos del cupo. Es un chequeo de salud rápido tras tocar la segmentación patrimonial (que depende del cupo). No escribe nada. Se corre con python -m scripts.diag_check_cupo.
Conecta con: Clientes.Comitentes (lectura), core.mongo. Custodia la fuente del segmento patrimonial (api.services.segmentacion).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
