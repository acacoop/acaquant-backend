Ingesta diaria del calendario económico global (FOMC, CPI, jobs, etc.) desde Finnhub, que devuelve ~60 días forward en un solo endpoint. Normaliza el "impact" a int (0-3) y hace upsert idempotente por (time, country, event).

Cron: 1×/día a las 06:00 UTC (L-V).

Conecta con: usa `core.finnhub.economic_calendar`, escribe `Market.EconomicCalendar`. Lo consume el router de market (calendario económico) en la API/home.
