---
id: scripts.diag_agro_otc
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_agro_otc.py
---

# scripts/diag_agro_otc

> scripts/diag_agro_otc.py — READ-ONLY: por qué se pierden futuros agro del volumen.

**Archivo:** `scripts/diag_agro_otc.py`

## Qué hace
Diagnóstico de solo lectura que mide cuántos boletos de futuros agro quedan FUERA del volumen AGRO y por qué motivo (financieros, OTC en instrumento/denominación, sin match de grano), replicando la lógica de clasificar_commodity sin tocar nada. Por motivo reporta boletos, cuentas distintas y toneladas en juego, para decidir con números si conviene incluir cuentas OTC o ajustar la clasificación antes de cambiar la función y backfillear. Se corre `python -m scripts.diag_agro_otc`.
Conecta con: CashFlow.NegocioMovimientos (lee), lógica de api.services.operaciones_informes.clasificar_commodity, core.mongo (read).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
