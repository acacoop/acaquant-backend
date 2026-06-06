---
id: api.services.comercial
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\comercial.py
---

# api/services/comercial

> api/services/comercial.py — Tablero Comercial (lente por operador).

**Archivo:** `api\services\comercial.py`

## Qué hace
Motor del Tablero Comercial (lente por operador, solo manager). Cruza todo por `id_cuenta`: QUIÉN (operador asignado + segmentación), ACTIVIDAD y ARANCEL (operaciones de mercado), VOLUMEN (boletos pesificados), TAMAÑO (último AuM) y el join operador↔usuario para detectar cuentas huérfanas. Devuelve el resumen por operador con buckets de estado comercial (ACTIVA/ENFRIANDOSE/DORMIDA/NUEVA). Cacheado on-the-fly.

Conecta con: lee `Clientes.Comitentes`, `CashFlow.Operaciones`, `CashFlow.NegocioMovimientos`, `Valuaciones.AuM` y `Manager.Users`; aplica `api.services._negocio_futuros`; lo consume el sub-router `/api/manager/comercial`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Clientes.ComercialCache]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
- [[db.Manager.Users]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.manager.checks]]  ·  _module_
- [[api.routers.manager.comercial]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.services.sin_operador]]  ·  _module_
- [[jobs.actividad_mensual]]  ·  _module_
- [[jobs.comercial_rollup]]  ·  _module_
