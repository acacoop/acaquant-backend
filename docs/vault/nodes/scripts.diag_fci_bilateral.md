---
id: scripts.diag_fci_bilateral
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_fci_bilateral.py
---

# scripts/diag_fci_bilateral

> Diag READ-ONLY para diseñar la unificación de las SOLICITUDES FCI bilateral

**Archivo:** `scripts/diag_fci_bilateral.py`

## Qué hace
Diagnóstico read-only que mide, con dato real, lo necesario antes de escribir el job que unifica las solicitudes FCI bilateral (hoy solo en NegocioMovimientos) dentro de Operaciones. Responde: cuántas solicitudes hay y cuántas líneas por comprobante (¿colapsar?), de dónde sale el bruto (importe vs cantidad), qué estado/op traen, y sobre todo si Operaciones YA trae esa liquidación (riesgo de doble conteo). No escribe nada. Se corre con python -m scripts.diag_fci_bilateral.
Conecta con: CashFlow.NegocioMovimientos, CashFlow.Operaciones, core.mongo. Insumo de diseño del job fci_bilateral.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
