---
id: core.byma
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core\byma.py
---

# core/byma

> Cliente BYMA Primarias Placements.

**Archivo:** `core\byma.py`

## Qué hace
Cliente del API BYMA Primarias Placements (colocaciones primarias). Hace OAuth2 client_credentials con token cacheado en memoria (refresh ante 401), rate-limit interno de 30 req/min y wrappers para underwriters, issuers, colocaciones históricas (paginadas) y descarga del documento asociado. Incluye `iter_pages` para recorrer toda la paginación y excepciones tipadas (BymaAuthError, BymaRateLimitError, etc.).

Conecta con: lee credenciales de `config` (BYMA_*); pega al API REST de BYMA; lo consumen jobs/scripts que ingestan datos de colocaciones primarias.

## Usa / conecta con →
- [[config]]  ·  _module_
