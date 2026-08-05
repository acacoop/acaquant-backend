---
id: api.services.canje
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/canje.py
---

# api/services/canje

> Serie histórica del canje CCL/MEP intra-bono (ej. AL30C / AL30D − 1).

**Archivo:** `api/services/canje.py`

## Qué hace
Arma la serie histórica del canje intra-bono: el mismo bono en sus dos especies de liquidación (C ~ CCL y D ~ MEP), cuyo spread mide la brecha cambiaria implícita. Por día devuelve precio_c, precio_d y canje. Para hoy, si el cron de cierre aún no corrió, agrega un punto live con el último trade. Optimizado: lee ~365 docs de cierre en vez de agregar ~540k ticks. No confundir con spread legislación (bonos distintos).

Conecta con: lee `Trading.CanjeCierre` (materializada por `jobs.cierre_canje`) y `Trading.TimeSales` para el punto live; pares definidos en `config.PARES_CANJE`; lo consume el endpoint de canje.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[config]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.services.copiloto.renta_fija]]  ·  _module_
