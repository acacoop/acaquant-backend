Cron L-V a 22:00 UTC (19:00 ART) que corre `jobs.bcra --today`: pide al BCRA las series CER/TAMAR/DOLAR/BADLAR (incluye hoy+21d de CER forward para los breakevens).

Conecta con: ejecuta `jobs/bcra.py`; escribe las series CER/TAMAR/etc a Mongo, consumidas por motor_curvas (CER T-10) y motor_breakevens. Timeout 15m.
