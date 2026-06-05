Cron L-V a 11:30 UTC (08:30 ART) que corre `jobs.descubrir_cuentas`: itera comitentes 1-12000 contra el broker (pyRofex) para descubrir qué cuentas están autorizadas para el user master. Timeout 90m (en el incidente del 2026-06-03 quedó colgado 5h); lock evita doble instancia.

Conecta con: ejecuta `jobs/descubrir_cuentas.py`; consulta el broker vía pyRofex y persiste el set de cuentas autorizadas en Mongo.
