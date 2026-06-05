---
id: scripts.seed_cedears
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/seed_cedears.py
---

# scripts/seed_cedears

> seed_cedears.py — upsert de CEDEARs en Trading.Cedears.

**Archivo:** `scripts/seed_cedears.py`

## Qué hace
Seed idempotente que upsertea el master de CEDEARs en Trading.Cedears: metadata categórica (nombre, sector, industria, región, país) por ticker. Es el espejo conceptual de Trading.Curvas pero para acciones vía CEDEAR; no toca market data, que vive aparte en Trading.CedearsSnapshot (escrito por engines/motor_cedears.py cada 1s). Alimenta la categorización del Scanner de Renta Variable. Uso: `python -m scripts.seed_cedears [--dry-run]`.

Conecta con: Trading.Cedears (escribe), engines.motor_cedears + Trading.CedearsSnapshot (market data), api.services.scanner (lo consume).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
