Configuración compartida de pytest. Su único trabajo es agregar la raíz del repo a `sys.path` para que los tests puedan importar `quant`, `jobs`, `api`, `core`, etc. sin instalar el proyecto como paquete. Es lo que permite correr `pytest` directo desde la raíz.

Conecta con: lo carga pytest automáticamente al arrancar; habilita los imports de todos los tests bajo `tests/unit/` y `tests/integration/`.
