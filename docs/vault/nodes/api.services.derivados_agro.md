---
id: api.services.derivados_agro
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\derivados_agro.py
---

# api/services/derivados_agro

> Service puro — Pase Agro (Trigo / Maíz / Soja Rosario).

**Archivo:** `api\services\derivados_agro.py`

## Qué hace
Service puro del Pase Agro (Trigo/Maíz/Soja Rosario). Dos capas: (1) la PIZARRA replica la planilla de la mesa con filas PIZARRA (manual), DISPO y N futuros live, calculando ars, pase y TNAV compuesta (validada contra la planilla); (2) el panel de opciones + simulador de estrategias que arma la cadena calls/puts por vencimiento y simula put sintético y long put (piso, zona expuesta, precio efectivo, diferencias). Nada se persiste.

Conecta con: lee `Trading.AgroSnapshot` (futuros) y `Trading.AgroOpcionesSnapshot` (opciones), escritos por `engines.motor_agro` / `engines.motor_agro_opciones`; usa el dólar oficial; lo consume el router `/api/derivados/agro`.

## Usa / conecta con →
- [[core.dolar_oficial]]  ·  _module_
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.derivados_agro]]  ·  _module_
