---
id: scripts.diag_fci_shape
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_fci_shape.py
---

# scripts/diag_fci_shape

> Diag READ-ONLY: shape exacto de los FCI bilateral ya cargados en Operaciones,

**Archivo:** `scripts/diag_fci_shape.py`

## Qué hace
Diagnóstico read-only que dumpea el shape exacto de los FCI bilateral ya cargados en CashFlow.Operaciones (campos de las liquidaciones CL, solicitudes DOC, distribución de prefijos de boleto) para que el job que los traiga desde NegocioMovimientos produzca documentos idénticos. También cruza por categoría FCI el prefijo de comprobante (CL=liquidación / DOC=solicitud) y la cobertura de importe. No escribe nada. Se corre con python -m scripts.diag_fci_shape.

Conecta con: CashFlow.Operaciones y CashFlow.NegocioMovimientos, cliente Mongo de solo lectura. Define el mapeo de campos del job de carga FCI.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
