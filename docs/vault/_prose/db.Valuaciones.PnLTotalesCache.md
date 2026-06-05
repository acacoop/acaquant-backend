Cache del PnL total por cuenta en la base `Valuaciones`. Precalcula el PnL (cost-basis weighted-average) de TODAS las cuentas para servir la vista sin recomputar el motor en cada request.

Conecta con: la escribe el cron `jobs/pnl_totales_precompute.py`; la lee `api/services/pnl.py`. Referenciada como singleton-friendly en `core/mongo.py`.
