Backfill que repara el bruto en $0 de todas las suscripciones/rescates FCI viejos, de forma SEGURA: recorre la historia por ventanas de N días (filtrando por el índice `fecha_categoria`, leyendo solo esa ventana) con sleep entre ventanas, para no competir con los motores ni tirar el CPU del M10. UPDATE puro por boleto (nunca inserta → no duplica), idempotente y re-corrible.
Se corre vía `run_job.sh` (lock+timeout), idealmente fuera de rueda: `python -m scripts.backfill_fci_bruto [--dry-run]`.
Conecta con: lee `CashFlow.NegocioMovimientos`, escribe `CashFlow.Operaciones`; usa `core.mongo`.
