---
id: jobs.sync_api_copies
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/sync_api_copies.py
---

# jobs/sync_api_copies

> sync_api_copies.py — re-sincroniza colecciones API derivadas.

**Archivo:** `jobs/sync_api_copies.py`

## Qué hace
Job orquestador que re-sincroniza las colecciones derivadas `*API.*API` (copias con contrato limpio para la API/dashboard) desde sus fuentes. Cada flag dispara una migración drop+insert: --aum, --assets, --flujo, --movimientos, --titulos, o --all. Se encadena al cron de la colección fuente correspondiente para que la copia API quede en sync apenas se actualiza el origen.

Conecta con: invoca las funciones migrate_* de `scripts.api_migrate`; lee `Valuaciones.AuM/Assets`, `CashFlow.Flujo/Movimientos`, `Trading.Curvas+Bonds` y escribe las copias `PortfolioAPI/TitulosAPI/OperacionesAPI`. Lo encadenan los crons de aum/cashflow/flujo.

_Sin conexiones detectadas mecánicamente._
