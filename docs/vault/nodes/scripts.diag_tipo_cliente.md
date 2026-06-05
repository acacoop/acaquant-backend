---
id: scripts.diag_tipo_cliente
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_tipo_cliente.py
---

# scripts/diag_tipo_cliente

> diag_tipo_cliente.py — read-only de Clientes.Comitentes.tipo_cliente.

**Archivo:** `scripts/diag_tipo_cliente.py`

## Qué hace
Diagnóstico read-only que imprime los valores únicos del campo `tipo_cliente` de Clientes.Comitentes con su count, ordenados de mayor a menor. Sirve para entender qué valores reales toma hoy (PH/PJ, null, vacío) antes de apoyar en él la lógica de segmentación patrimonial. No escribe nada. Se corre con `python -m scripts.diag_tipo_cliente`.
Conecta con: lee Clientes.Comitentes; apoya el diseño de api.services.segmentacion (docs/SEGMENTACION_PATRIMONIAL.md).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
