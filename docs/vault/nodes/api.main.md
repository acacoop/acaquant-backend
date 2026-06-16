---
id: api.main
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\main.py
---

# api/main

> TradingAV API — FastAPI entrypoint.

**Archivo:** `api\main.py`

## Qué hace
Entrypoint de la API FastAPI (`uvicorn api.main:app`). Arma la app: monta todos los routers (`analitica`, `carteras`, `cotizaciones`, `cuentas`, `manager`, `market`, `ordenes`, `risk`, `scanner`, etc.), aplica auth global (`verify_api_key`), rate-limit (slowapi), gzip y profiling opt-in. En el `lifespan` valida la postura de auth (fail-closed en prod si falta `API_KEY`), hace warmup del pool Mongo y lanza el sampler de recursos; monta el sub-app MCP si está configurado.

Conecta con: importa `api.auth`, `api.deps`, `api.ratelimit`, `api.profiling`, todos los `api.routers.*` y `api.mcp`; pinguea ambos singletons de `core.mongo` al arrancar.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.deps]]  ·  _module_
- [[api.mcp]]  ·  _module_
- [[api.mcp.auth]]  ·  _module_
- [[api.mcp.discovery]]  ·  _module_
- [[api.mcp.oauth]]  ·  _module_
- [[api.mcp.server]]  ·  _module_
- [[api.profiling]]  ·  _module_
- [[api.ratelimit]]  ·  _module_
- [[api.routers]]  ·  _module_
- [[api.routers.analitica]]  ·  _module_
- [[api.routers.back_office]]  ·  _module_
- [[api.routers.carteras]]  ·  _module_
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.derivados_agro]]  ·  _module_
- [[api.routers.derivados_sinteticos]]  ·  _module_
- [[api.routers.ingest]]  ·  _module_
- [[api.routers.manager]]  ·  _module_
- [[api.routers.manager_resources]]  ·  _module_
- [[api.routers.market]]  ·  _module_
- [[api.routers.me]]  ·  _module_
- [[api.routers.news]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.routers.valuaciones]]  ·  _module_
- [[config]]  ·  _module_
- [[core.mongo]]  ·  _module_
