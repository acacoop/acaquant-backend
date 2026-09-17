---
name: add-agente
description: Sumar un agente al ASISTENTE (un modelo con sus herramientas, sus señales de ruteo, su familia y sus claves de foco), o una herramienta a un agente que ya existe. Cubre las cuatro piezas que lo dejan conectado — el archivo del agente, la tarea en el ruteo, el registro y los tests — sin tocar el grafo. Doc que manda: docs/AvAgentAI.md §10.
---

# Sumar un agente al ASISTENTE

Se aplica cuando el usuario pide «que el asistente también sepa de X»,
«agregá una herramienta que traiga Y» o «quiero que pueda contestar Z». Doc
que manda: `docs/AvAgentAI.md` (§3 los agentes, §6 el ruteo, §9 el tiempo, §10 cómo agregar).

## 0. Antes de escribir: dos preguntas

1. **¿Es un agente nuevo o una herramienta de uno que existe?** Un agente es un
   tema con su propio proveedor y su propia frontera de datos (cuenta = datos
   del negocio, mercado = datos públicos). Si la función nueva habla del mismo
   tema y ve los mismos datos, es una herramienta más del agente que existe:
   una función en su archivo, sumada a `herramientas` de su `AGENTE`, y listo.
2. **¿Ve datos de una persona?** Entonces su tarea lleva `traza_sin_texto`.
   Con qué modelo corre lo elige el panel del LAB, no el código. Y toda
   consulta de cuentas lleva `{permitido.FILTRO_SQL}` (test).

## 1. El archivo del agente: `asistente/agentes/<nombre>.py`

Las herramientas y el agente, juntos:

```python
from asistente.agente import COMUN, Agente

def mi_herramienta(arg: str, limit: int = 10) -> dict:
    """Qué hace. Qué NO es (cuál es la parecida). Qué devuelve."""
    ...
    return {"filas": [...], "cuantos": n, "truncado": n > limit, "_tabla": {...}}

def _instruccion(_foco: dict) -> str:
    return COMUN + "\nLo propio de este agente.\n"

AGENTE = Agente(
    nombre="<nombre>",
    tarea="asistente_<nombre>",
    describe="una línea que el ruteo lee para saber cuándo mandarle la pregunta",
    instruccion=_instruccion,
    herramientas=(mi_herramienta,),  # puede nacer vacío: el agente existe y dice que no puede consultar
    senales=("palabra1", "palabra2"), # ESPECÍFICAS, palabras enteras sin acentos; las genéricas van en la familia
    familia="mercado",                # o "" si no pertenece a ninguna
    foco=("cuenta",),                 # claves del foco que lee y aprende: "cuenta", "ticker"; () si no tiene llave
)
```

¿Cuál es el SUJETO del agente? Cartera y cliente hablan de UNA cuenta (la llave
es obligatoria en sus herramientas). Operaciones habla de la mesa (la cuenta es
un filtro opcional). Los de mercado, de un instrumento. Eso decide la firma. El
tiempo va según la convención de `docs/AvAgentAI.md` §9.

Reglas de una herramienta: dict siempre; errores como `{"error": ..., "que_hacer": ...}`;
topes declarados como constantes y `truncado` cuando recorta; totales en SQL;
claves con `_` son para la pantalla. La ficha (docstring + firma) entra en
`herramientas.MAX_FICHA_CHARS` (test).

## 2. La tarea: `core/modelos.TAREAS`

```python
"asistente_<nombre>": {"tier": "flash", "max_tokens": 3000, "timeout_s": 120,
                       "usa_herramientas": True,
                       "para_que": "el asistente, agente <NOMBRE>: …"},
```

`"traza_sin_texto": True` si ve contacto, documento o cualquier dato de una
persona (la traza guarda tokens y latencia, no texto). Nada de proveedor ni
modelo: eso se elige en el panel.

## 3. El registro: `asistente/agentes/__init__.py`

Una línea: importar el módulo y sumar `<modulo>.AGENTE` a la tupla de `AGENTES`.
Si es de una familia nueva, la entrada en `FAMILIAS` con sus señales genéricas.
Nada más cambia: el grafo lo enchufa solo, el ruteo lee su `describe` y sus
`senales`, el panel del LAB muestra su tarea.

## 4. Tests y doc

- `tests/unit/test_asistente.py`: la herramienta con datos falsos (patch de
  `get_pool` del módulo del agente), y una pregunta de punta a punta con
  `_correr(...)` sumando la herramienta a `_TOOLS`.
- `docs/AvAgentAI.md` §3 (tabla de agentes), §7 (tabla de tareas).
- `docs/MAPA_APP.md`: el conteo de tareas en la fila de `ia.config`, si cambió.

## 5. Verificar

```bash
python -m pytest -q tests/unit/test_asistente.py && ruff check . && python -c "import api.main"
python -m scripts.diag_herramienta <mi_herramienta> --arg valor   # la corre sin modelo
```

`test_todo_agente_esta_registrado_y_declarado` falla si el archivo existe y falta
cualquiera de las piezas 2 o 3. Lo que depende del proveedor real (que el
modelo pida la herramienta, que la respuesta sea buena) se prueba en el LAB.
