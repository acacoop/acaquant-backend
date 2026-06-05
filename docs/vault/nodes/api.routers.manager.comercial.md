---
id: api.routers.manager.comercial
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/comercial.py
---

# api/routers/manager/comercial

> Sub-router Manager → /api/manager/comercial — Tablero Comercial.

**Archivo:** `api/routers/manager/comercial.py`

## Qué hace
Sub-router `/api/manager/comercial` — Tablero Comercial (lente por operador). Devuelve el resumen comercial por operador (# cuentas, activas/dormidas según días sin operar, AuM, etc.) y un debug auditable del cálculo de `nivel_3`/segmento patrimonial para una cuenta puntual (cupo crudo + MEP/UVA usado + cálculo paso a paso + actual vs recalculado, con los umbrales PH/PJ). Gateado por `manager_comercial` (accesible a `asistente_comercial`).

Conecta con: services `comercial::resumen_por_operador`, `segmentacion::clasificar_nivel_3`, `macro::get_ultimo_mep/uva`; lee `Clientes.Comitentes` vía `api.db.get_db_clientes`. Ver docs/TABLERO_COMERCIAL.md.

## Usa / conecta con →
- [[api.db]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.segmentacion]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_
- [[db.Trading.UVA]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
