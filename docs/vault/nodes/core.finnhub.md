---
id: core.finnhub
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/finnhub.py
---

# core/finnhub

> Cliente Finnhub con rate limiting interno.

**Archivo:** `core/finnhub.py`

## Qué hace
Cliente del API Finnhub con rate-limit interno thread-safe (40 req/min, bajo el tope real de 60 del free tier, para no ir a 429). Expone wrappers de alto nivel para los endpoints usados: noticias generales y por empresa, quotes, velas de equity y forex, calendario económico y profile de compañía. Excepciones tipadas (FinnhubError); el caller decide reintento/log.

Conecta con: lee `FINNHUB_API_KEY` de `config`; pega a `finnhub.io/api/v1`; lo consumen `jobs/news_finnhub.py`, `jobs/economic_calendar.py` y `jobs/market_quotes.py`.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.market]]  ·  _module_
- [[jobs.adr_live]]  ·  _module_
- [[jobs.market_quotes]]  ·  _module_
- [[jobs.news_finnhub]]  ·  _module_
