---
id: scripts.backfill_mep_operaciones
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_mep_operaciones.py
---

# scripts/backfill_mep_operaciones

> scripts/backfill_mep_operaciones.py — estampa `mep` (dólar de la fecha) en

**Archivo:** `scripts/backfill_mep_operaciones.py`

## Qué hace
Backfill reusable e idempotente que estampa el campo `mep` (dólar de la fecha de concertación) en cada doc de CashFlow.Operaciones, para poder dolarizar la vista MOVIMIENTOS (bruto/arancel ÷ mep). Es eficiente: agrupa por fecha distinta y hace un updateMany por fecha, no 488k updates uno por uno. Las fechas sin MEP en Valuaciones.Dolar quedan sin tocar y se reportan, así re-correr tras backfillear el dólar las completa. Se corre `python -m scripts.backfill_mep_operaciones` (o `--dry-run` para solo medir cobertura).
Conecta con: CashFlow.Operaciones (escribe), Valuaciones.Dolar (lee vía api.services._mep.get_mep_for_date), core.mongo.

## Usa / conecta con →
- [[api.services._mep]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
