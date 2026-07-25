---
id: jobs.aum
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/aum.py
---

# jobs/aum

> jobs/aum.py — CLIENTE Aunesa (librería, no es un job).

**Archivo:** `jobs/aum.py`

## Qué hace
El cron diario de AuM: autentica contra Aunesa, trae el listado de cuentas activas y consulta en paralelo (8 workers) la posición valuada de cada una a T+2. Procesa cada respuesta (agrupa por especie, aplica reglas de exclusión de `_aum_filters`, valúa según tipo de instrumento: renta fija ÷100, futuros +1, resto directo) y persiste idempotentemente. Al cerrar sincroniza unidades nuevas hacia Assets y pre-materializa el resumen FCI.

Conecta con: pega a Aunesa, escribe `Valuaciones.AuM`; sincroniza `Valuaciones.Assets` + `TitulosAPI.AssetsAPI`; llama `jobs.aum_resumen_fci.sync_fecha`. Helpers reusados por `aum_backfill` y `aum_backfill_historico`.

## Usa / conecta con →
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[jobs.portafolio_backfill]]  ·  _module_
- [[jobs.portafolio_reparar_timeouts]]  ·  _module_
