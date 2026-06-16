---
id: api.services.operaciones_informes
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\operaciones_informes.py
---

# api/services/operaciones_informes

> operaciones_informes.py — normalización + ingesta a CashFlow.Operaciones.

**Archivo:** `api\services\operaciones_informes.py`

## Qué hace
Normaliza e ingiere operaciones a `CashFlow.Operaciones`, la fuente de verdad de operaciones (boletos de la API "informes" de Aunesa, que no incluye movimientos administrativos ni FCI bilateral). Mapea headers heterogéneos (API e histórico en Excel) a 10 campos canónicos y persiste idempotente: un boleto = un documento, con índice único sobre `boleto`. Service puro, sin FastAPI.

Conecta con: escribe en `CashFlow.Operaciones`; usa `api.services._mep` para pesificar; lo invoca `api/routers/manager/operaciones.py` (backfill por CSV desde la UI) y a futuro el job de ingesta diaria.

## Usa / conecta con →
- [[api.services._mep]]  ·  _module_
- [[core.postgres]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager.operaciones]]  ·  _module_
- [[jobs.fci_bilateral]]  ·  _module_
- [[jobs.operaciones_informes]]  ·  _module_
