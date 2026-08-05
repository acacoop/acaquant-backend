---
id: api.services.rv_motor
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/rv_motor.py
---

# api/services/rv_motor

> api/services/rv_motor.py — motor de la Mesa de Estrategia (Renta Variable).

**Archivo:** `api/services/rv_motor.py`

## Qué hace
Motor de la Mesa de Estrategia (Renta Variable): primera pieza del feature, calcula la matriz de correlación de retornos diarios del universo de activos USD. Es la base del hedge-finder (correlación de un activo vs el resto) y de la optimización de carteras. Trabaja sobre el precio del subyacente USD (no el CEDEAR en ARS), alineando las series por fecha sobre una ventana común. Cacheado.

Conecta con: lee `Trading.Cedears` (master de tickers/underlying) y `Trading.PreciosAcciones` (precios del subyacente USD); usa `quant.rolling_stats`. Lo invoca el router/tab de Renta Variable.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[quant.rolling_stats]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.renta_variable]]  ·  _module_
- [[api.services.day_trading]]  ·  _module_
