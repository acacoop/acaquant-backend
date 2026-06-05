---
id: jobs.partner_export
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\partner_export.py
---

# jobs/partner_export

> partner_export.py — exporta posiciones de cuentas puntuales a ACAPortfolio.Cartera.

**Archivo:** `jobs\partner_export.py`

## Qué hace
Job autónomo que alimenta la Partner API externa: pega DIRECTO a Aunesa por las cuentas de `config.PARTNER_EXPORT_CUENTAS` y vuelca su posición valuada. Replica el cálculo de valuación de `jobs/aum.py` (divisor 100 para renta fija, +1 para futuros) pero SIN aplicar los filtros de exclusión del AuM — el proveedor ve todas las posiciones. Idempotente por (fecha, id_cuenta); `fecha` es día hábil ARG. Corre 2×/día (18:30 y 23:00 ART, L-V).

Conecta con: lee posiciones directo de Aunesa (sesión propia, no usa `core.aunesa`), escribe `ACAPortfolio.Cartera`. Esa colección la sirve `partner_api` a través de `data.acaquant.com`. No depende de `Valuaciones.AuM`.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.ACAPortfolio.Cartera]]  ·  _collection_

## Lo usan (backlinks) ←
- [[cron.jobs.partner_export]]  ·  _cron_
