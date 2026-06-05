---
id: jobs.aum_resumen_fci
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\aum_resumen_fci.py
---

# jobs/aum_resumen_fci

> aum_resumen_fci.py — pre-materializa Valuaciones.AuMResumenFCI.

**Archivo:** `jobs\aum_resumen_fci.py`

## Qué hace
Pre-materializa el resumen de tenencias FCI por fecha: para cada `fecha_snapshot` agrupa las posiciones de fondos por unidad y guarda UN solo doc por fecha (array de unidades con valuación total y número de cuentas), minimizando transporte por cursor. Excluye la cuenta 255 (trading propio). Se invoca al final de `jobs.aum` y `jobs.aum_backfill`; el modo CLI sirve para reconstrucción puntual o backfill total.

Conecta con: lee `Valuaciones.AuM` + `Valuaciones.Assets` (unidades con CARTERA FCI), escribe `Valuaciones.AuMResumenFCI`. Lo consume la vista de AuM/FCI de la API.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.Assets]]  ·  _collection_
- [[db.Valuaciones.AuM]]  ·  _collection_

## Lo usan (backlinks) ←
- [[jobs.aum]]  ·  _module_
- [[jobs.aum_backfill]]  ·  _module_
