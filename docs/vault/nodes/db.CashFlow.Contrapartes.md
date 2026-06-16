---
id: db.CashFlow.Contrapartes
type: collection
layer: db
repo: infra
tags: [collection, db, infra]
---

# CashFlow.Contrapartes

> Colección Mongo en DB CashFlow.

## Qué hace
Maestro de contrapartes (clientes/cuentas comitentes vistas como contraparte de la mesa) en la base `CashFlow`. Sirve de catálogo para enriquecer flujos y operaciones con datos de la contraparte, y para segmentación.

Conecta con: la pueblan/usan `jobs/flujo_contrapartes.py` y `api/services/segmentacion.py`, `api/services/risk.py`, `api/services/operaciones_informes.py`; se expone vía `api/routers/cuentas.py` y `api/routers/operaciones.py`. Tiene copia derivada en `CuentasAPI.ContrapartesAPI`.

## Lo usan (backlinks) ←
- [[api.routers.cuentas]]  ·  _module_
- [[api.services.risk]]  ·  _module_
