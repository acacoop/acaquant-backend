---
id: engines.forwards
type: module
layer: engines
repo: backend
tags: [module, engines, backend]
path: engines\forwards.py
---

# engines/forwards

> main_forwards.py — Motor de tasas forward en tiempo real.

**Archivo:** `engines\forwards.py`

## Qué hace
Motor de tasas forward en tiempo real. Cada 30s lee la última TEA por ticker, las agrupa por curva (tasa fija / CER) y calcula la matriz NxN de tasas forward implícitas entre cada par de vencimientos. Escribe tanto la versión live como el cierre diario.

Conecta con: lee las TEA de `Trading.MarketSnapshot.metrics` (escritas por `engines.curvas`) y la definición de curvas vía `engines._curvas_loader`; escribe `Trading.ForwardsLive` (1 doc por curva, live) y `Trading.ForwardsHistorico` (1 doc por fecha+curva). Lo invoca systemd `motor_forwards.service`. Lo consume `api.services.derivados` (forwards); su z-score lo calcula `jobs.forwards_zscore`.

## Usa / conecta con →
- [[core.market_snapshot]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_
- [[engines._curvas_loader]]  ·  _module_

## Lo usan (backlinks) ←
- [[svc.motor_forwards]]  ·  _service_
