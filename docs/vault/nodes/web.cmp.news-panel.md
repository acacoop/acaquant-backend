---
id: web.cmp.news-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\news-panel.tsx
---

# web/components/news-panel

**Archivo:** `src\components\news-panel.tsx`

## Qué hace
Panel de titulares económicos argentinos agregados de RSS, estilo Bloomberg (cada fuente con su color de acento), con filtro por categoría (economía/finanzas/mercados). Pollea cada 60s; al hacer click en un titular abre el lector inline.

Conecta con: fetch a los headlines del backend (`News.Headlines`, poblada por `jobs.news_ingesta` / `jobs.news_finnhub`); abre `news-reader` para el modo lectura. Se usa en `home-view`.

## Usa / conecta con →
- [[api.routers.news]]  ·  _module_

## Lo usan (backlinks) ←
- [[web.cmp.home-view]]  ·  _component_
