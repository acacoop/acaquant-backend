Backfill que setea `nivel_3 = "PJ GRANDE"` en las Comitentes activas cuyo `id_cuenta` está en `CashFlow.Contrapartes` (FCI / sociedades gerentes / etc.). Aplica a los docs existentes la regla de negocio que ya vive en `api/services/segmentacion.py` (si es contraparte → siempre PJ GRANDE, sin importar cupo/UVA). Solo toca esas cuentas; server-side update_many, idempotente, dry-run por default.
Se corre con `python -m scripts.backfill_contrapartes_pj_grande [--apply]`.
Conecta con: `api.services.segmentacion.cargar_ids_contrapartes`; escribe `Clientes.Comitentes`.
