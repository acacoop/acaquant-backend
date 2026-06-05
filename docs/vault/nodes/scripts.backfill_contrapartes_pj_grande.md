---
id: scripts.backfill_contrapartes_pj_grande
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/backfill_contrapartes_pj_grande.py
---

# scripts/backfill_contrapartes_pj_grande

> Backfill: nivel_3 = "PJ GRANDE" para las Comitentes activas cuyo id_cuenta

**Archivo:** `scripts/backfill_contrapartes_pj_grande.py`

## Qué hace
Backfill que setea `nivel_3 = "PJ GRANDE"` en las Comitentes activas cuyo `id_cuenta` está en `CashFlow.Contrapartes` (FCI / sociedades gerentes / etc.). Aplica a los docs existentes la regla de negocio que ya vive en `api/services/segmentacion.py` (si es contraparte → siempre PJ GRANDE, sin importar cupo/UVA). Solo toca esas cuentas; server-side update_many, idempotente, dry-run por default.
Se corre con `python -m scripts.backfill_contrapartes_pj_grande [--apply]`.
Conecta con: `api.services.segmentacion.cargar_ids_contrapartes`; escribe `Clientes.Comitentes`.

## Usa / conecta con →
- [[api.services.segmentacion]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
