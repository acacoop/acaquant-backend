---
id: api.routers.analitica
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/analitica.py
---

# api/routers/analitica

> Router Analítica — Tier 1 + Tier 2 tools del asistente expuestas como HTTP.

**Archivo:** `api/routers/analitica.py`

## Qué hace
Router `/api/analitica`: expone como HTTP las herramientas analíticas Tier 1 + Tier 2 (listar curva, serie macro, descomposición de retorno, sensibilidad, canje, carry trade, comparar inversión, opciones). Son thin wrappers para consumo externo (acaquant-web, curl, debugging); el asistente legacy las llamaba directo por service registry sin loopback HTTP.

Conecta con: delega en los services `renta_fija`, `macro`, `descomposicion_retorno`, `sensibilidad`, `canje`, `carry_trade`, `comparar_inversion`, `opciones`, `analitica`; lo monta `api.main`.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.macro_sql]]  ·  _module_
- [[api.services.opciones_sql]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.renta_fija_sql]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
