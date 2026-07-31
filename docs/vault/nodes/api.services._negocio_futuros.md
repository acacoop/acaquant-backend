---
id: api.services._negocio_futuros
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\_negocio_futuros.py
---

# api/services/_negocio_futuros

> Filtro de exclusión de futuros para queries sobre CashFlow.NegocioMovimientos.

**Archivo:** `api\services\_negocio_futuros.py`

## Qué hace
Filtro chico y reutilizable que excluye los futuros ROFEX DLR (los que llegan con `unidad == "USDL"`) de las consultas sobre `CashFlow.NegocioMovimientos`. Esos futuros no pagan arancel propio del proyecto, así que si no se filtran inflan el volumen por operador, los gráficos de NEGOCIO y el matching de aranceles. Expone `match_no_futuros()`, un sub-doc `$match` para spread en pipelines. Hay que mantenerlo en sync con `jobs/_aum_filters.py`.

Conecta con: lo importan `api.services.comercial`, `api.services.aunesa_aranceles` y los endpoints de `/operaciones/negocio`; filtra docs de `CashFlow.NegocioMovimientos`.

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.services.aunesa_aranceles]]  ·  _module_
