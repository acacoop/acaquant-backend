Cron que corre `jobs.news_ingesta` (RSS de medios económicos argentinos): primer tiro a 11:25 UTC (post-resume de Atlas) y luego cada 15 min de 12-23 UTC, todos los días.

Conecta con: ejecuta `jobs/news_ingesta.py`; parsea feeds RSS y escribe a `News.Headlines`, leído por el router `/api/news`. Timeout 5m.
