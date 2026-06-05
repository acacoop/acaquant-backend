---
id: scripts.audit_operaciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/audit_operaciones.py
---

# scripts/audit_operaciones

> scripts/audit_operaciones.py — auditoría READ-ONLY de CashFlow.Operaciones.

**Archivo:** `scripts/audit_operaciones.py`

## Qué hace
Diagnóstico read-only (Fase 0 de calidad de datos) que mide la cobertura real de `CashFlow.Operaciones` y la reconcilia contra `NegocioMovimientos` (verdad de referencia del feed consolidadosGenerales). No escribe ni filtra: clasifica lo que falta por categoría/mes/moneda para distinguir hueco real de lo que legítimamente `/informes` no trae (cauciones, FCI bilateral, admin). Cruza boleto↔comprobante crudo y normalizado a dígitos.
Se corre con `python -m scripts.audit_operaciones [--desde 2025-01-01]`.
Conecta con: lee `CashFlow.Operaciones` y `CashFlow.NegocioMovimientos`; usa `core.mongo` read-only.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
