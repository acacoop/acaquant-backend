---
id: jobs.forwards_zscore
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\forwards_zscore.py
---

# jobs/forwards_zscore

> forwards_zscore.py — coeficientes (media, desvío) por par de la matriz de forwards.

**Archivo:** `jobs\forwards_zscore.py`

## Qué hace
Calcula los coeficientes (media y desvío muestral) de cada celda de la matriz de tasas forward, sobre los últimos 30 días hábiles de histórico. El z-score NO se persiste: el front lo computa en cada refresh con el live (`z = (forward_live − media) / desvío`), así el numerador se mueve cada 30s y el denominador queda fijo hasta el próximo cron. Omite pares con n_obs < 20 o desvío ~0.

Cron: 20:30 UTC (17:30 ART), post-cierre del motor de forwards. `--dry` solo imprime.

Conecta con: lee `Trading.ForwardsHistorico`, escribe `Trading.ForwardsZscore`. Lo consume la matriz de forwards en el front (que combina estos coeficientes con el live del motor).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.forwards_zscore]]  ·  _cron_
