Cron cada 30 min en la rueda (:05 y :35, 15-22 UTC L-V) que corre `jobs.pnl_totales_precompute`: precalcula el PnL de TODAS las cuentas (~883) para que la API lo sirva cacheado en vez de computar el motor pesado on-the-fly. Timeout 25m + lock para no apilarse.

Conecta con: ejecuta `jobs/pnl_totales_precompute.py`; usa el motor `api/services/pnl.py`; escribe a `Valuaciones.PnLTotalesCache`, leído por la API.
