---
id: core.mae
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/mae.py
---

# core/mae

> Cliente MAE MarketData.

**Archivo:** `core/mae.py`

## Qué hace
Cliente HTTP del mercado MAE (Mercado Abierto Electrónico). Autentica con header `x-api-key`, elige base URL prod o UAT según la env var `MAE_ENV`, y se auto-limita a 30 requests/minuto para no gatillar bloqueos. Hoy wrappea las cotizaciones de Repo (`/mercado/cotizaciones/repo`, paginado) con error handling tipado (auth, rate-limit, red). Diseñado genérico para sumar cauciones/títulos/acciones después sin tocar la infra de auth.

Conecta con: la API REST de MAE (`api.mae.com.ar`); lee `MAE_API_KEY` y `MAE_ENV` de `config.py`. Lo invocan jobs/services que necesitan datos de repo/caución del MAE.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[tests.unit.test_mae_client]]  ·  _module_
