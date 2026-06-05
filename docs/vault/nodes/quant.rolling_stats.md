---
id: quant.rolling_stats
type: module
layer: quant
repo: backend
tags: [module, quant, backend]
path: quant/rolling_stats.py
---

# quant/rolling_stats

> rolling_stats.py — beta / alpha / correlación / vol realizada

**Archivo:** `quant/rolling_stats.py`

## Qué hace
Estadística de series de precios: convierte precios a retornos aritméticos y calcula volatilidad realizada anualizada (√252), correlación de Pearson, z-score del último retorno y la regresión OLS asset-vs-benchmark (beta, alpha anualizado, R²). Funciones puras sobre listas de floats en memoria; la lectura de la serie histórica la hace el caller.

Conecta con: lo importan `api/services/rv_motor.py` (Mesa de Estrategia RV) y `api/services/scanner.py` (Scanner CEDEARs), que le pasan series leídas de `Trading.PreciosAcciones`/feeds y reciben beta/alpha/vol/correlación para sus tablas. No toca Mongo.

## Lo usan (backlinks) ←
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
