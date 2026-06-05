---
id: tests.unit
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/__init__.py
---

# tests/unit/__init__

**Archivo:** `tests/unit/__init__.py`

## Qué hace
Marcador de paquete de los tests unitarios (`tests/unit/__init__.py`). Acá vive el grueso de la red de seguridad: pruebas puras y mockeadas (sin Mongo ni red real) sobre quant, services y clientes externos. Es lo que corre el CI en cada push.

Conecta con: lo corre `pytest -ra` por default (el `addopts` de pyproject excluye integration); cubre módulos de `quant/`, `api/services/`, `core/` y `jobs/`.

_Sin conexiones detectadas mecánicamente._
