---
id: api.services._negocio_arancelables
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\_negocio_arancelables.py
---

# api/services/_negocio_arancelables

> Filtro de `op` no arancelables para CashFlow.NegocioMovimientos.

**Archivo:** `api\services\_negocio_arancelables.py`

## Qué hace
Define la lista canónica de tipos de operación (`op`) que por naturaleza NO son arancelables (cobros del emisor, susc/rescates de FCI sin comisión, aperturas de caución, dividendos, etc.) y un helper `match_solo_arancelables()` que devuelve el `$match` para excluirlos. Match exacto en `op`, no substring.

Conecta con: filtra queries sobre `CashFlow.NegocioMovimientos`; lo usan el endpoint `/api/manager/aunesa/boletos/faltantes` (para no mostrarlos como "sin arancel") y `aunesa_aranceles.run_backfill` (para no contarlos como `sin_match`).

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[api.services.aunesa_aranceles]]  ·  _module_
