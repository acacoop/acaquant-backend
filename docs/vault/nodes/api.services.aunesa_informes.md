---
id: api.services.aunesa_informes
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/aunesa_informes.py
---

# api/services/aunesa_informes

> api/services/aunesa_informes.py — aranceles por boleto desde Aunesa /operaciones/informes.

**Archivo:** `api/services/aunesa_informes.py`

## Qué hace
Cliente puntual de Aunesa `/operaciones/informes` que trae el ÚNICO dato que el feed de negocio no tiene: el arancel por boleto. Devuelve `{boleto: {moneda: arancel}}` para enriquecer por `boleto == comprobante`. Incluye un parser robusto de montos que tolera los dos formatos que manda Aunesa (coma argentina vs punto decimal) y toma el arancel una sola vez por boleto (no suma las filas repetidas por ejecución).

Conecta con: pega a Aunesa vía `core.aunesa`; lo consume `api.services.aunesa_aranceles` para el backfill de aranceles sobre `CashFlow.NegocioMovimientos`.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.aunesa]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.services.aunesa_aranceles]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
