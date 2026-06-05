Cron diario a 11:30 UTC (post-resume de Atlas) que corre `jobs.economic_calendar`: ingesta del calendario económico global (vía Finnhub) para el módulo Market.

Conecta con: ejecuta `jobs/economic_calendar.py`; usa `core/finnhub.py` y persiste el calendario a Mongo, leído por el router `/api/market`. Timeout 5m.
