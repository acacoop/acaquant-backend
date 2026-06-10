---
id: core.cafci
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/cafci.py
---

# core/cafci

> Extracción del código CAFCI desde un string `unidad`.

**Archivo:** `core/cafci.py`

## Qué hace
Helper minúsculo: extrae el código CAFCI (ej. `CAFCI3580-1199`) de un string `unidad` con un regex. Las unidades de FCI vienen como `[<id>] CAFCI<n>-<m> - <descripción>`, y ese código es el identificador real del fondo, lo que matchea con el `ticker` parseado de los boletos.

Conecta con: lo usan `jobs/aum.py` (auto-fill al sincronizar `Valuaciones.Assets`) y `scripts/backfill_assets_cafci.py`; sirve de puente entre `Valuaciones.AuM`/`Assets` y `CashFlow.NegocioMovimientos`.

## Lo usan (backlinks) ←
- [[jobs.aum]]  ·  _module_
