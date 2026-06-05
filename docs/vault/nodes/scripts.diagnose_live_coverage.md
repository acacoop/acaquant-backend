---
id: scripts.diagnose_live_coverage
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diagnose_live_coverage.py
---

# scripts/diagnose_live_coverage

> diagnose_live_coverage.py — diagnóstico de cobertura live para

**Archivo:** `scripts/diagnose_live_coverage.py`

## Qué hace
Diagnóstico read-only que mide cuánta cobertura "live" hay para valuar posiciones desde MarketSnapshot en vez de confiar en el precio del AuM. Para cada unidad con tenencia revisa si existe en Valuaciones.Assets, si tiene INSTRUMENTO seteado y si ese instrumento aparece en Trading.MarketSnapshot, e imprime conteos por bucket + los gaps más grandes por valuación para priorizar dónde completar metadata. Se corre con `python -m scripts.diagnose_live_coverage [--top N]`.
Conecta con: lee Valuaciones.AuM, Valuaciones.Assets y Trading.MarketSnapshot; informa el live-fallback de api.services.portfolio.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
