---
id: tests.conftest
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/conftest.py
---

# tests/conftest

> Configuración compartida de pytest.

**Archivo:** `tests/conftest.py`

## Qué hace
Configuración compartida de pytest. Su único trabajo es agregar la raíz del repo a `sys.path` para que los tests puedan importar `quant`, `jobs`, `api`, `core`, etc. sin instalar el proyecto como paquete. Es lo que permite correr `pytest` directo desde la raíz.

Conecta con: lo carga pytest automáticamente al arrancar; habilita los imports de todos los tests bajo `tests/unit/` y `tests/integration/`.

_Sin conexiones detectadas mecánicamente._
