---
id: tests.unit.test_byma_client
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_byma_client.py
---

# tests/unit/test_byma_client

> Tests unitarios del cliente BYMA (sin red real).

**Archivo:** `tests/unit/test_byma_client.py`

## Qué hace
Valida el cliente del API de BYMA Primarias (Placements) sin red real (todo mockeado). Cubre el flujo OAuth2 (fetch de token, cache, expiración y refresh forzado, errores de credenciales), el helper `_get_json` (happy path, retry en 401, error en 429, respuesta no-JSON), la paginación automática con `iter_pages`, los endpoints públicos (underwriters, placements históricos, descarga de documentos en bytes) y el rate limiter interno.

Conecta con: blinda `core/byma.py`; el cliente lo usan los flujos que consumen colocaciones primarias de BYMA.

## Usa / conecta con →
- [[core.byma]]  ·  _module_
