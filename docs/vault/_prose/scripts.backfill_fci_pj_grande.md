Backfill que setea `nivel_3 = "PJ GRANDE"` para los Fondo Común de Inversión en `Clientes.Comitentes`, usando `tipo_cliente == "Fondo Común de Inversión"` (señal fresca del sync de Aunesa, más confiable que `CashFlow.Contrapartes`, que estaba desactualizada). Solo toca esas cuentas. El dry-run lista las cuentas que matchean para revisarlas antes de escribir; server-side update_many, idempotente.
Se corre con `python -m scripts.backfill_fci_pj_grande [--apply]`.
Conecta con: escribe `Clientes.Comitentes`; complementa `backfill_contrapartes_pj_grande` y la segmentación patrimonial.
