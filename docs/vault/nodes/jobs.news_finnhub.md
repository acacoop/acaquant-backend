---
id: jobs.news_finnhub
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\news_finnhub.py
---

# jobs/news_finnhub

> news_finnhub.py — ingesta de noticias desde Finnhub.

**Archivo:** `jobs\news_finnhub.py`

## Qué hace
Job de ingesta de noticias desde Finnhub: noticias globales, FX y M&A (categorías generales) más company-news de los ADRs argentinos/LATAM core de la mesa (GGAL, YPF, BMA, VIST, etc.). Normaliza cada item a un headline con título, excerpt, fecha y fuente "Finnhub". Cron sugerido cada 30 min en horario de mercado US.

Conecta con: lee de la API de Finnhub vía `core.finnhub` (general_news/company_news); escribe en `News.Headlines` con dedup por índice único sobre `url` (mismo destino que `jobs.news_ingesta`). Lo consume el router `/api/news`.

## Usa / conecta con →
- [[core.finnhub]]  ·  _module_
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.news_finnhub]]  ·  _cron_
