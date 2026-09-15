---
name: add-mundo
description: Sumar un mundo al ASISTENTE (un agente con sus herramientas y sus señales de despacho), o una herramienta a un mundo que ya existe. Cubre las cuatro piezas que lo dejan conectado — el archivo del mundo, la tarea en el ruteo, el registro y los tests — sin tocar el grafo. Doc que manda: docs/AvAgentAI.md §7.
---

# Sumar un mundo al ASISTENTE

Se aplica cuando el usuario pide «que el asistente también sepa de X»,
«agregá una herramienta que traiga Y» o «quiero que pueda contestar Z». Doc
que manda: `docs/AvAgentAI.md` (§3 el paquete, §5 el despacho, §7 cómo agregar).

## 0. Antes de escribir: dos preguntas

1. **¿Es un mundo nuevo o una herramienta de uno que existe?** Un mundo es un
   tema con su propio proveedor y su propia frontera de datos (cuenta = datos
   del negocio, mercado = datos públicos). Si la función nueva habla del mismo
   tema y ve los mismos datos, es una herramienta más del mundo que existe:
   una función en su archivo, sumada a `herramientas` de su `AGENTE`, y listo.
2. **¿Ve datos del negocio?** Entonces su tarea lleva `datos: "negocio"` y va a
   un proveedor que no entrena. Y toda consulta de cuentas lleva
   `{permitido.FILTRO_SQL}` (test).

## 1. El archivo del mundo: `asistente/mundos/<nombre>.py`

Las herramientas y el agente, juntos:

```python
from asistente.agente import COMUN, Agente

def mi_herramienta(arg: str, limit: int = 10) -> dict:
    """Qué hace. Qué NO es (cuál es la parecida). Qué devuelve."""
    ...
    return {"filas": [...], "cuantos": n, "truncado": n > limit, "_tabla": {...}}

def _instruccion(_foco: dict) -> str:
    return COMUN + "\nLo propio de este mundo.\n"

AGENTE = Agente(
    nombre="<nombre>",
    tarea="asistente_<nombre>",
    describe="una línea que el despacho lee para saber cuándo mandarle la pregunta",
    instruccion=_instruccion,
    herramientas=(mi_herramienta,),
    senales=("raiz1", "raiz2"),      # minúsculas, sin acentos; raíces de palabra
)
```

Reglas de una herramienta: dict siempre; errores como `{"error": ..., "que_hacer": ...}`;
topes declarados como constantes y `truncado` cuando recorta; totales en SQL;
claves con `_` son para la pantalla. La ficha (docstring + firma) entra en
`herramientas.MAX_FICHA_CHARS` (test).

## 2. La tarea: `core/modelos.TAREAS`

```python
"asistente_<nombre>": {"tier": "flash", "max_tokens": 3000, "timeout_s": 120,
                       "usa_herramientas": True,
                       "para_que": "el asistente, mundo <NOMBRE>: …"},
```

`"proveedor": "openai", "datos": "negocio"` si ve cuentas.

## 3. El registro: `asistente/mundos/__init__.py`

Una línea: importar el módulo y sumar `<modulo>.AGENTE` a la tupla de `MUNDOS`.
Nada más cambia: el grafo lo enchufa solo, el despacho lee su `describe` y sus
`senales`, el panel del LAB muestra su tarea.

## 4. Tests y doc

- `tests/unit/test_asistente.py`: la herramienta con datos falsos (patch de
  `get_pool` del módulo del mundo), y una pregunta de punta a punta con
  `_correr(...)` sumando la herramienta a `_TOOLS`.
- `docs/AvAgentAI.md` §2 (tabla de nodos), §3 (el paquete), §6 (tabla de tareas).
- `docs/MAPA_APP.md`: el conteo de tareas en la fila de `ia.config`, si cambió.

## 5. Verificar

```bash
python -m pytest -q tests/unit/test_asistente.py && ruff check . && python -c "import api.main"
python -m scripts.diag_herramienta <mi_herramienta> --arg valor   # la corre sin modelo
```

`test_todo_mundo_esta_registrado_y_declarado` falla si el archivo existe y falta
cualquiera de las piezas 2 o 3. Lo que depende del proveedor real (que el
modelo pida la herramienta, que la respuesta sea buena) se prueba en el LAB.
