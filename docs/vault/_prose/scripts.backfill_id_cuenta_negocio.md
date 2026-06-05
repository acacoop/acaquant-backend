Backfill que denormaliza `id_cuenta` (extraído de `cuenta` = "[805] NOMBRE" por regex) en los docs existentes de `CashFlow.NegocioMovimientos`, para que la vista COMERCIAL filtre por índice en vez de regex sobre `cuenta`. Server-side (un único update_many con pipeline, sin traer docs al cliente); solo toca los que aún no tienen `id_cuenta`; crea también los índices. Idempotente.
Se corre con `python -m scripts.backfill_id_cuenta_negocio [--dry-run]`.
Conecta con: escribe `CashFlow.NegocioMovimientos`; habilita el Tablero Comercial (`api.services.comercial`).
