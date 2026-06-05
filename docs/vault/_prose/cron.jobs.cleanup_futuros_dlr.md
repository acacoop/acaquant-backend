Cron L-V a 12:30 UTC (09:30 ART, antes de abrir los motores) que corre `jobs.cleanup_futuros_dlr`: purga los contratos DLR ya vencidos de `Trading.FuturosDLRSnapshot`.

Conecta con: ejecuta `jobs/cleanup_futuros_dlr.py`; escribe (delete) en `Trading.FuturosDLRSnapshot`, colección que alimenta motor_futuros_dlr. Timeout 10m.
