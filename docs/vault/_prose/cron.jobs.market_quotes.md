Cron cada 1 minuto en horario US (13-21 UTC, L-V) que corre `jobs.market_quotes`: cotizaciones de equity + forex para los watchlists de la HOME. Timeout 50s (< intervalo) porque yfinance no trae timeout propio y un Yahoo lento apilaba procesos.

Conecta con: ejecuta `jobs/market_quotes.py`; usa `core/yahoo.py` (yfinance) y persiste las quotes a Mongo, leídas por el router `/api/market`.
