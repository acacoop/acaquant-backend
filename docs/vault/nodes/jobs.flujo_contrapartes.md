---
id: jobs.flujo_contrapartes
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\flujo_contrapartes.py
---

# jobs/flujo_contrapartes

**Archivo:** `jobs\flujo_contrapartes.py`

## Qué hace
Trae de Aunesa (endpoint /operaciones/informes) los boletos de las contrapartes con cuenta asignada y los persiste en `CashFlow.Flujo`. Excluye cauciones colocadoras y futuros financieros, infiere moneda de las condiciones y deduplica por boleto. Re-chequea una ventana de los últimos 7 días (idempotente: borra esos días y re-inserta) para cubrir cambios tardíos / fines de semana largos.

El delete se hace recién tras un fetch exitoso, para no perder datos si Aunesa falla.

Conecta con: pega a la API de Aunesa (login + informes), lee `CashFlow.Contrapartes` (qué cuentas mirar), escribe `CashFlow.Flujo`. Lo consume la vista de flujo de contrapartes en /operaciones.

## Usa / conecta con →
- [[config]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.flujo_contrapartes]]  ·  _cron_
