Cron diario a 12:00 UTC (09:00 ART) que corre `jobs.argentina_datos`: pega a argentinadatos.com y persiste indicadores macro AR públicos (riesgo país, IPC, REM) en Mongo.

Conecta con: ejecuta `jobs/argentina_datos.py`; usa `core/argentina_datos.py`; escribe las series macro consumidas por los servicios `macro`/`argy`/`rem`. Timeout 15m.
