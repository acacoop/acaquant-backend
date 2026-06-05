---
id: scripts.backfill_aranceles
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_aranceles.py
---

# scripts/backfill_aranceles

> backfill_aranceles.py — CLI manual del backfill de aranceles.

**Archivo:** `scripts/backfill_aranceles.py`

## Qué hace
Thin wrapper CLI del backfill de aranceles: ofrece --dry-run/--apply, logs en disco y prints de progreso para correr desde el Droplet. La lógica core vive en `api/services/aunesa_aranceles.py` (compartida con el endpoint Manager `POST /api/manager/aunesa/boletos/backfill`). Pega aranceles por boleto desde Aunesa `/operaciones/informes` a `CashFlow.NegocioMovimientos`.
Se corre con `python -m scripts.backfill_aranceles --desde ... --hasta ... --apply` (acotable por --cuenta).
Conecta con: `api.services.aunesa_aranceles.run_backfill`; escribe `CashFlow.NegocioMovimientos`.

## Usa / conecta con →
- [[api.services.aunesa_aranceles]]  ·  _module_
