---
id: api.services.carry_trade
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/carry_trade.py
---

# api/services/carry_trade

> Serie de carry trade en USD para una curva (tasa_fija / cer).

**Archivo:** `api/services/carry_trade.py`

## Qué hace
Calcula la serie de carry trade en USD de cada bono ARS de una curva (tasa_fija o cer): el retorno acumulado en pesos descontando la variación del MEP (o CCL) en el mismo período, con fórmula exacta `(1+ret_ars)/(1+var_dolar)−1`. Sirve para ver si el carry en pesos le gana o no a la devaluación implícita. Output día por día, listo para line chart.

Conecta con: lee precios diarios de `Trading` (cierre + live-fallback) y la serie de MEP de `Valuaciones.Dolar`; lo consume el endpoint de carry trade.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services._sql]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[db.Trading.DOLAR]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
