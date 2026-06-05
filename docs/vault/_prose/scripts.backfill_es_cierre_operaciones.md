Backfill que materializa el campo booleano `es_cierre` en los docs existentes de `CashFlow.Operaciones` (marca cierres de caución, que no entran a las sumatorias de `/ops/*`). Server-side (update_many + pipeline, sin cursor); recrea el índice `moneda_escierre_concertacion` y dropea el redundante. ORDEN crítico de deploy: correrlo ANTES de reiniciar la API, o `/ops/*` dejaría afuera los boletos viejos.
Se corre con `python -m scripts.backfill_es_cierre_operaciones [--dry-run]`.
Conecta con: `api.services.operaciones_informes.ensure_indexes`; escribe `CashFlow.Operaciones`.
