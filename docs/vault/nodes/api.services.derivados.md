---
id: api.services.derivados
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/derivados.py
---

# api/services/derivados

> Capa de servicio — derivados (futuros DLR, forwards, breakevens).

**Archivo:** `api/services/derivados.py`

## Qué hace
Capa de servicio que reúne las magnitudes derivadas de la curva local y los únicos derivados puros (futuros DLR de ROFEX): futuros DLR, forwards y breakevens. Separa por dominio de `renta_fija.py` (allá los cash bonds, acá lo que se construye sobre ellos). `get_futuros_dlr` filtra contratos ya vencidos que el motor deja como fantasma. Cacheado con TTLs cortos.

Conecta con: lee snapshots escritos por `engines.futuros_dlr`, `engines.forwards` y `engines.breakevens` (`Trading.FuturosDLRSnapshot` y colecciones de forwards/breakevens); lo consume el router `/api/derivados`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.server]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[scripts.perf_sweep]]  ·  _module_
