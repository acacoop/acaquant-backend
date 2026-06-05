Marcador de paquete del árbol de tests (`tests/__init__.py`). No contiene lógica: solo hace que pytest y Python traten la carpeta `tests/` como paquete importable. Toda la suite (unit + integration) cuelga de acá.

Conecta con: agrupa `tests/unit/` y `tests/integration/`; la configuración de descubrimiento vive en `tests/conftest.py` y en `pyproject.toml`.
