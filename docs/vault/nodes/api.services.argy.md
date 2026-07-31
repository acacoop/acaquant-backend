---
id: api.services.argy
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\argy.py
---

# api/services/argy

> Capa de servicio — métricas argentinas con returns calculados.

**Archivo:** `api\services\argy.py`

## Qué hace
Arma el agregado del panel ARGY del frontend: las 5 métricas argentinas (MEP, CCL, canje, caución ARS, caución USD), cada una con su valor live y las variaciones %Día / %7d / %MTD / %YTD calculadas contra el cierre histórico anclado a cada fecha-target. Toma el live del snapshot y matchea cada anchor al último cierre con fecha ≤ target.

Conecta con: lee live de `Valuaciones.DolarSnapshot` y `Trading.CaucionSnapshot` (escritos por `engines.dolares` / `engines.caucion`) e histórico de `Valuaciones.Dolar` y `Trading.Caucion`; lo consume el router `/api/argy`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services._sql]]  ·  _module_
- [[api.services.mercado_hist_sql]]  ·  _module_
- [[core]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.eikon_bonos]]  ·  _module_
- [[core.series_macro]]  ·  _module_
- [[db.Trading.DOLAR]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.services.copiloto.home]]  ·  _module_
