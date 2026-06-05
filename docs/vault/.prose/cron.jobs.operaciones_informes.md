Cron intra-día (cada 30 min, 13:30-22:00 UTC L-V) que corre `jobs.operaciones_informes`: ingesta de operaciones desde Aunesa /informes (fuente de verdad por concertación) a `CashFlow.Operaciones`, idempotente con lookback.

Conecta con: ejecuta `jobs/operaciones_informes.py`; usa `core/aunesa.py` y `api/services/operaciones_informes.py`; escribe a `CashFlow.Operaciones`, leído por las series de `/ops`. Timeout 25m. Recuperar un día: `--desde Y-M-D --hasta Y-M-D`.
