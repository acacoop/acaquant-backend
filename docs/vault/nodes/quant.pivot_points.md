---
id: quant.pivot_points
type: module
layer: quant
repo: backend
tags: [module, quant, backend]
path: quant\pivot_points.py
---

# quant/pivot_points

> pivot_points.py — Floor Trader Pivot Points sobre mercado.precios_acciones (SQL).

**Archivo:** `quant\pivot_points.py`

## Qué hace
Calcula Floor Trader Pivot Points (PP, R1-R3, S1-S3) sobre el OHLC del período previo, en 4 timeframes a la vez (diario/semanal/mensual/anual). Los rangos temporales son dinámicos —siempre "el período anterior cerrado"— nunca hardcodea fechas. Expone también una variante verbosa (`debug_4_timeframes`) que muestra velas usadas y la fórmula con números reales.

Conecta con: lee `Trading.PreciosAcciones` en Mongo (import diferido de `core.mongo` para respetar la regla de capas). Lo invoca `api/services/scanner.py` (`obtener_4_timeframes`) para la vista Scanner de Renta Variable, y `api/routers/manager/checks.py` (`debug_4_timeframes`) para el panel Manager → Validaciones.

## Usa / conecta con →
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.checks]]  ·  _module_
- [[api.services.copiloto]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_
- [[api.services.trading_pivots]]  ·  _module_
