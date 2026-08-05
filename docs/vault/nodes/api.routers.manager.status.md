---
id: api.routers.manager.status
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/status.py
---

# api/routers/manager/status

> GET /api/manager/status — estado unificado de motores y jobs batch.

**Archivo:** `api/routers/manager/status.py`

## Qué hace
Sub-router `/api/manager/status` — un GET que devuelve el estado unificado de salud del sistema: por cada motor de mercado, job batch y API externa, lee el último dato escrito en su colección y lo clasifica (ok / lento / crítico / fuera_rueda / sin_datos) según un umbral de antigüedad y si estamos en rueda. Corre los chequeos en paralelo con un ThreadPoolExecutor.

Conecta con: lee el timestamp más reciente de muchas colecciones (`Trading.TimeSales/MarketSnapshot/ForwardsLive/BreakevensLive/CedearsSnapshot/...`, `Opciones.OptionsSnapshot`, `Valuaciones.AuM/DolarOficialLive`, `CashFlow.NegocioMovimientos`, `News.Headlines`, etc.). Solo lectura, no recalcula. Lo consume la tab STATUS de la manager-view.

## Usa / conecta con →
- [[api.routers.manager._common]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[db.Trading.DOLAR]]  ·  _collection_
- [[db.Trading.MarketSnapshot]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
