---
id: scripts.diag_cuentas_faltantes
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_cuentas_faltantes.py
---

# scripts/diag_cuentas_faltantes

> diag_cuentas_faltantes.py — detecta cuentas que faltan en snapshots de AuM.

**Archivo:** `scripts/diag_cuentas_faltantes.py`

## Qué hace
Diagnóstico read-only que detecta cuentas que quedaron afuera de un backfill de AuM, usando la data misma como fuente de verdad (los logs no son confiables). Por cada snapshot mensual compara su set de cuentas contra las que estaban en el snapshot anterior Y en el posterior: si una cuenta aparece antes y después pero falta en el del medio, es un "hueco" casi seguro de un backfill que no la levantó. Se corre con python -m scripts.diag_cuentas_faltantes [--meses 2026-01-31,...].
Conecta con: Valuaciones.AuM (lectura, fecha_snapshot/id_cuenta), core.mongo. Detecta huecos para re-correr el backfill de jobs.aum.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.AuM]]  ·  _collection_
