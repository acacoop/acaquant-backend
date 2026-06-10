---
id: jobs.news_ingesta
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/news_ingesta.py
---

# jobs/news_ingesta

> news_ingesta.py — Ingesta de RSS de medios económicos argentinos.

**Archivo:** `jobs/news_ingesta.py`

## Qué hace
Job de ingesta de RSS de medios económicos argentinos (Ámbito, Cronista, iProfesional, Clarín, Infobae, La Nación). Parsea cada feed con feedparser, limpia el HTML del excerpt y guarda las headlines con dedup por URL. Si un feed rompe el XML, lo loggea y sigue con el resto. Corre cada 15 min por cron.

Conecta con: escribe en `News.Headlines` (índice único en `url` + índices por fecha/fuente/categoría), comparte colección con `jobs.news_finnhub`. Lo consume el router `/api/news` (feed agregado + reader mode).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.news_ingesta]]  ·  _cron_
