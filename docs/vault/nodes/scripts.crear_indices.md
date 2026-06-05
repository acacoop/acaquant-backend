---
id: scripts.crear_indices
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/crear_indices.py
---

# scripts/crear_indices

> crear_indices.py — Crea índices en MongoDB para mejorar performance.

**Archivo:** `scripts/crear_indices.py`

## Qué hace
Herramienta reusable que crea de un saque todos los índices de performance de las colecciones del sistema (TimeSales, MarketSnapshot, Curvas, AuM, Assets, Flujo, Opciones, las copias *API, ActividadMensual, JobRuns con TTL, etc.). Seguro: no modifica datos, solo agrega índices; idempotente (si ya existen, no hace nada). Se corre `python -m scripts.crear_indices` típicamente al setup o tras agregar colecciones.
Conecta con: índices sobre Trading, Valuaciones, CashFlow, Opciones, CuentasAPI, OperacionesAPI, PortfolioAPI, TitulosAPI, Manager, Clientes; core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Trading.MarketSnapshot]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_
- [[db.Valuaciones.Dolar]]  ·  _collection_
