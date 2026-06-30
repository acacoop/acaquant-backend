---
id: jobs.segmentar_patrimonial
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/segmentar_patrimonial.py
---

# jobs/segmentar_patrimonial

> segmentar_patrimonial.py — re-clasifica `nivel_3` de todas las Comitentes activas.

**Archivo:** `jobs/segmentar_patrimonial.py`

## Qué hace
Job que re-clasifica el `nivel_3` (segmento patrimonial) de todas las comitentes activas. Lee `tipo_cliente` + `cupo.transaccional_ars` y aplica las reglas de `api.services.segmentacion` usando MEP y UVA del momento. Las PJ quedan sin clasificar (nivel_3=null) hasta que se ingeste la serie UVA. Idempotente (solo escribe si el label cambió); dry-run por default, requiere `--apply` para escribir.

Conecta con: lee `Clientes.Comitentes` + contrapartes de `CashFlow` (vía `cargar_ids_contrapartes`), toma MEP/UVA de `api.services.macro`, escribe `nivel_3` en `Clientes.Comitentes`. Doc: `docs/SEGMENTACION_PATRIMONIAL.md`.

## Usa / conecta con →
- [[api.services.macro]]  ·  _module_
- [[api.services.segmentacion]]  ·  _module_
- [[core.postgres]]  ·  _module_
