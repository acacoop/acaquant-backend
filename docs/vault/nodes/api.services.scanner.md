---
id: api.services.scanner
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\scanner.py
---

# api/services/scanner

> api/services/scanner.py — vista Scanner del módulo Renta Variable.

**Archivo:** `api\services\scanner.py`

## Qué hace
Vista Scanner del módulo Renta Variable: un row por CEDEAR activo con su categoría (sector/industria/región/país) y métricas operativas (last, intradía %, vs 1D %, y retorno "real" en USD descontando la devaluación implícita del CCL). Lee el CCL una sola vez por request y lo aplica a todos los tickers. Cacheado 5s para soportar polling del frontend.

Conecta con: joina `Trading.Cedears` (master categórico) con `Trading.CedearsSnapshot` (live de `engines.motor_cedears`, cada 1s) y usa el CCL live de `Valuaciones.DolarSnapshot`/`Dolar`. Lo invoca el router `/api/scanner`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.scanner]]  ·  _module_
- [[api.services.copiloto]]  ·  _module_
- [[api.services.scanner_sql]]  ·  _module_
