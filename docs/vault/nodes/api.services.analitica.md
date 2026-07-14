---
id: api.services.analitica
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\analitica.py
---

# api/services/analitica

> Capa de servicio — analítica Tier 2 sobre data existente.

**Archivo:** `api\services\analitica.py`

## Qué hace
Capa de analítica "Tier 2" sobre la renta fija ya calculada. Tres herramientas: `snapshot_curva_historico` (la curva entera tal como cerró un día pasado), `calcular_pendiente_curva` (slope en bps de una métrica, con comparación contra otra fecha) y `liquidez_secundario` (volumen del día vs promedio de N ruedas). Aplica el patrón live-fallback: lee `Trading.SnapshotsCierre` y, si falta el día, agrega `TimeSales`. Resultados cacheados (TTL 300s).

Conecta con: lee `Trading.SnapshotsCierre`, `Trading.Curvas` y `Trading.TimeSales`; reusa `api.services.renta_fija`; lo invocan el router de analítica/MCP y `api.services.descomposicion_retorno`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services._sql]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[core]]  ·  _module_
- [[core.curvas_sql]]  ·  _module_
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.mcp.tools.parked_mercado]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
