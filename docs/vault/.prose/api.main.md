Entrypoint de la API FastAPI (`uvicorn api.main:app`). Arma la app: monta todos los routers (`analitica`, `carteras`, `cotizaciones`, `cuentas`, `manager`, `market`, `ordenes`, `risk`, `scanner`, etc.), aplica auth global (`verify_api_key`), rate-limit (slowapi), gzip y profiling opt-in. En el `lifespan` valida la postura de auth (fail-closed en prod si falta `API_KEY`), monta el sub-app MCP si está configurado.

Conecta con: importa `api.auth`, `api.deps`, `api.ratelimit`, `api.profiling`, todos los `api.routers.*` y `api.mcp`; pinguea ambos singletons de `core.mongo` al arrancar.
