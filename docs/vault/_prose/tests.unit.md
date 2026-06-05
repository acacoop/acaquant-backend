Marcador de paquete de los tests unitarios (`tests/unit/__init__.py`). Acá vive el grueso de la red de seguridad: pruebas puras y mockeadas (sin Mongo ni red real) sobre quant, services y clientes externos. Es lo que corre el CI en cada push.

Conecta con: lo corre `pytest -ra` por default (el `addopts` de pyproject excluye integration); cubre módulos de `quant/`, `api/services/`, `core/` y `jobs/`.
