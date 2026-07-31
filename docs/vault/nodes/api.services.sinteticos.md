---
id: api.services.sinteticos
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\sinteticos.py
---

# api/services/sinteticos

> Sintéticos — combinaciones LECAP/DLK + futuro DLR.

**Archivo:** `api\services\sinteticos.py`

## Qué hace
Arma dos tablas de sintéticos combinando bonos con el futuro DLR, matcheando por año-mes de vencimiento (toma automáticamente cualquier ticker nuevo): (1) LONG ROFEX + LONG LECAP = sintético en dólares (comparás USD invertidos vs USD obtenidos, sale TE y TNA); (2) SHORT ROFEX + LONG DLK = lock de tasa en pesos. Cacheado.

Conecta con: lee precios de LECAP/DLK de `Trading.Curvas`/snapshots y los precios de futuros DLR, más el dólar oficial live de `core.dolar_oficial`. Lo invoca el router `/api/derivados/sinteticos`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.dolar_oficial]]  ·  _module_
- [[core.market_snapshot]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.derivados_sinteticos]]  ·  _module_
- [[api.services.agro_cobertura]]  ·  _module_
- [[jobs.snapshot_sinteticos]]  ·  _module_
