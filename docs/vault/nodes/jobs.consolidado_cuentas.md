---
id: jobs.consolidado_cuentas
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\consolidado_cuentas.py
---

# jobs/consolidado_cuentas

> consolidado_cuentas.py — precalcula la valuación consolidada por cuenta.

**Archivo:** `jobs\consolidado_cuentas.py`

## Qué hace
Precalcula la valuación consolidada por cuenta (valor + base 100 + PnL acumulado en ARS y USD) que necesita la vista TOTALES > POR CUENTA. Hacerlo en vivo recorría N cuentas y reventaba el timeout HTTP (502): este job lo computa offline sin límite de tiempo y lo persiste con swap atómico.

Corre como cron diario después del AuM final (post `jobs.aum`, 23 UTC L-V).

Conecta con: llama `api.services.valuaciones::construir_consolidado`, escribe `Valuaciones.ConsolidadoCuentas`. El endpoint `/api/valuaciones/consolidado` solo lee esa colección (instantáneo).

## Usa / conecta con →
- [[api.services.valuaciones]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.consolidado_cuentas]]  ·  _cron_
