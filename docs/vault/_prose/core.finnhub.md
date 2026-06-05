Cliente del API Finnhub con rate-limit interno thread-safe (40 req/min, bajo el tope real de 60 del free tier, para no ir a 429). Expone wrappers de alto nivel para los endpoints usados: noticias generales y por empresa, quotes, velas de equity y forex, calendario económico y profile de compañía. Excepciones tipadas (FinnhubError); el caller decide reintento/log.

Conecta con: lee `FINNHUB_API_KEY` de `config`; pega a `finnhub.io/api/v1`; lo consumen `jobs/news_finnhub.py`, `jobs/economic_calendar.py` y `jobs/market_quotes.py`.
