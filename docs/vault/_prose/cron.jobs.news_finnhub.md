Cron que corre `jobs.news_finnhub` (noticias globales + ADRs AR/LATAM vía Finnhub): primer tiro a 11:25 UTC (post-resume) y luego cada 30 min de 12-23 UTC, todos los días.

Conecta con: ejecuta `jobs/news_finnhub.py`; usa `core/finnhub.py` y escribe a `News.Headlines`, leído por el router `/api/news`. Timeout 10m.
