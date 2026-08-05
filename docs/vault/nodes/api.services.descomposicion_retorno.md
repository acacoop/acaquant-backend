---
id: api.services.descomposicion_retorno
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/descomposicion_retorno.py
---

# api/services/descomposicion_retorno

> descomposicion_retorno.py — Atribución carry / rolldown / cambio_tasa.

**Archivo:** `api/services/descomposicion_retorno.py`

## Qué hace
Atribuye el retorno de un bono a sus componentes carry / rolldown / cambio_tasa, para tasa_fija (Lecap+Boncap, pesos, base TEM) y cer (donde trabaja con paridad y TEA real, y compone aparte el accrual de indexación). Dos entradas: `descomposicion_realizada` (ex-post entre dos fechas) y `rolldown_esperado` (ex-ante a un horizonte). La curva CER de interpolación usa solo Lecers para no ensuciar el rolldown con cupones.

Conecta con: usa `api.services.analitica` para snapshots de curva, lee la serie del CER de `Trading.CER` y la mediana del REM vía `api.services.rem`; lo consume el endpoint de descomposición/atribución.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[core.series_macro]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.services.copiloto.renta_fija]]  ·  _module_
