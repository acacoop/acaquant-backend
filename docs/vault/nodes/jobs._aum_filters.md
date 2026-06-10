---
id: jobs._aum_filters
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/_aum_filters.py
---

# jobs/_aum_filters

> Reglas de exclusión aplicadas a `Valuaciones.AuM`.

**Archivo:** `jobs/_aum_filters.py`

## Qué hace
Fuente única de las reglas de exclusión del AuM: define qué posiciones NO se contabilizan como patrimonio bajo administración. Cinco reglas: cash USDL, patrones OTC/CDC, cuentas de fondos por `id_cuenta` (leídas de `CuentasAPI.ContrapartesAPI`), nombres de contraparte por palabra completa (leídos de `CashFlow.Contrapartes`) y el cash ARS de las cuentas propias [100]/[101]. Expone `is_excluded()` (fila a fila) y `mongo_match_excluded()` (filtro `$or` para borrados masivos).

Conecta con: lee `CuentasAPI.ContrapartesAPI` y `CashFlow.Contrapartes`; lo usan `jobs.aum` (al persistir) y `scripts/cleanup_aum_excluidos.py` (limpieza retroactiva), garantizando criterio idéntico.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Contrapartes]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.services.sin_operador]]  ·  _module_
- [[jobs.aum]]  ·  _module_
