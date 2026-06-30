---
id: api.services.macro
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/macro.py
---

# api/services/macro

> Capa de servicio — series macro y clasificación.

**Archivo:** `api/services/macro.py`

## Qué hace
Capa de servicio de series macro: devuelve cada variable (tamar, cer, dólar, badlar, mep, o `<TICKER>.<CAMPO>`) como valor actual + serie histórica + estadísticos + una clasificación textual ("alto/bajo/normal"). Algunas variables están bloqueadas por falta de data y devuelven un stub con hint. Funciones cacheadas (`@cached`).

- `obtener_serie_macro` y `clasificar_nivel` son las dos tools que consume el asistente/analítica.

Conecta con: lee `Trading.<BADLAR/CER/…>` y `Trading.MarketSnapshot.metrics`, usa `quant.stats` para los estadísticos; lo invocan los routers `/api/cotizaciones/*` y `/api/analitica`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[core]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[db.Trading.DOLAR]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[jobs.segmentar_patrimonial]]  ·  _module_
