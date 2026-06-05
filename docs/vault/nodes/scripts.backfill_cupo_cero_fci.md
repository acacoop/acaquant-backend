---
id: scripts.backfill_cupo_cero_fci
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_cupo_cero_fci.py
---

# scripts/backfill_cupo_cero_fci

> Backfill: cupo en 0 (transaccional + usado) para los Fondo Común de Inversión.

**Archivo:** `scripts/backfill_cupo_cero_fci.py`

## Qué hace
Backfill que pone el `cupo` en 0 (transaccional + usado + utilizacion_pct) para los Fondo Común de Inversión en `Clientes.Comitentes`, ya que un FCI no tiene cupo de fondeo real. Target acotado a `tipo_cliente == "Fondo Común de Inversión"`; solo toca esas cuentas. Server-side update_many, idempotente, dry-run por default.
Se corre con `python -m scripts.backfill_cupo_cero_fci [--apply]`.
Conecta con: escribe `Clientes.Comitentes`; alimenta el cálculo de segmentación patrimonial.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
