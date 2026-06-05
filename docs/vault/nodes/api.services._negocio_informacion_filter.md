---
id: api.services._negocio_informacion_filter
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/_negocio_informacion_filter.py
---

# api/services/_negocio_informacion_filter

> Filtro de exclusión por `informacion` para CashFlow.NegocioMovimientos.

**Archivo:** `api/services/_negocio_informacion_filter.py`

## Qué hace
Lista canónica de substrings de `informacion` que NO deben entrar a `CashFlow.NegocioMovimientos` por ser movimientos administrativos/regulatorios (Bonificación, recuperos devengados, Gestión de cobranza, Márgenes MtR, Diferencias - Comit) que ensucian el control comercial. Expone `es_excluido(informacion)` para la ingesta y `match_excluir_informacion()` (regex Mongo) para el cleanup. Es la fuente de verdad única — editar acá propaga a todos los consumidores.

Conecta con: lo usa `api.services.aunesa_negocio` (descarta antes del upsert que hace `jobs.negocio_movimientos`) y `scripts/cleanup_negocio_informacion.py` (borra lo ya persistido en `CashFlow.NegocioMovimientos`).

## Lo usan (backlinks) ←
- [[api.services.aunesa_negocio]]  ·  _module_
- [[scripts.cleanup_negocio_informacion]]  ·  _module_
