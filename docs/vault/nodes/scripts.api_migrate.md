---
id: scripts.api_migrate
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/api_migrate.py
---

# scripts/api_migrate

> Script para crear y migrar colecciones API desde las colecciones existentes.

**Archivo:** `scripts/api_migrate.py`

## Qué hace
Herramienta reusable de infra que re-sincroniza las colecciones `*API` derivadas (drop + insert por contrato de API) desde las colecciones fuente. Normaliza campos como `cuenta` "[139] NOMBRE" → `id_cuenta` + `nombre`. Cada sub-comando migra un par: accionistas, contrapartes, flujo, movimientos, aum, assets, flujos-titulos.
Se corre con `python -m scripts.api_migrate <cmd>`. Es el equivalente manual del encadenado automático de `jobs.sync_api_copies`.
Conecta con: lee CashFlow/Valuaciones/Trading y escribe las copias `*API.*API`; usa `core.mongo`.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Contrapartes]]  ·  _collection_
- [[db.CuentasAPI.AccionistasAPI]]  ·  _collection_
- [[db.CuentasAPI.ContrapartesAPI]]  ·  _collection_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_

## Lo usan (backlinks) ←
- [[jobs.sync_api_copies]]  ·  _module_
