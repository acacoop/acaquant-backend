---
id: scripts.diag_aranceles_breakdown
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aranceles_breakdown.py
---

# scripts/diag_aranceles_breakdown

> Diag READ-ONLY: de dónde sale el arancel en cada fuente (para entender el delta

**Archivo:** `scripts/diag_aranceles_breakdown.py`

## Qué hace
Diagnóstico de solo lectura que desglosa el Σ arancel en cada fuente para entender el delta de la migración NegocioMovimientos (viejo) → Operaciones (nuevo): en Operaciones agrupa por tipo_operacion/mercado/operacion, en NegocioMovimientos por op/categoria, con totales por fuente para reconciliar contra el diag por operador. No escribe. Se corre `python -m scripts.diag_aranceles_breakdown`.
Conecta con: CashFlow.Operaciones y CashFlow.NegocioMovimientos (lee arancel), apoya la migración de aranceles de api.services.comercial, core.mongo (read).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
