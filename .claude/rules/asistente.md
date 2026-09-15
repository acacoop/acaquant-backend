---
paths:
  - "asistente/**"
  - "docs/AvAgentAI.md"
  - "tests/unit/test_asistente.py"
  - "core/modelos.py"
  - "core/traza.py"
---
# EL ASISTENTE — lo que hay que saber antes de tocarlo

> **Doc única: `docs/AvAgentAI.md`** ([VIVO]: se actualiza en el mismo commit).
> Skill para sumar un mundo: `add-mundo`. Mundos hoy: cartera (qué TIENE una
> cuenta), cliente (quién ES), operaciones (qué HIZO la mesa), mercado (qué HAY).

## El mapa

Un nodo del grafo = un módulo. `mundos/<nombre>.py` = las herramientas de un
mundo + su `AGENTE`, en un solo archivo; `mundos/__init__.py::MUNDOS` es el
registro. `despacho.py` decide qué mundos van (reglas primero, modelo después),
`junta.py` cruza, `grafo.py` enchufa. El resto son piezas de un solo uso:
`memoria`, `estado`, `puerta`, `control`, `esquema`, `permitido`, `sesiones`, `panel`.

## Las invariantes (cada una tiene un test)

1. **Nada nuevo queda suelto.** TODO archivo en `mundos/` es un mundo: tiene
   `AGENTE`, está en `MUNDOS`, su tarea está en `core/modelos.TAREAS`, y tiene
   señales (lo compartido va en `agente.py`, no en un helper ahí). Puede no
   tener herramientas todavía: entonces contesta que no puede consultarlo, sin
   modelo. Sumar un mundo NO toca `grafo.py`.
2. **Todo dato sale de una herramienta.** Docstring = descripción, firma =
   esquema. Errores como dato (`{"error": ...}`), nunca excepción. Claves con
   `_` no viajan al modelo. Toda consulta de cuentas lleva `permitido.FILTRO_SQL`.
3. **Un mundo ve solo lo suyo.** La memoria lleva marcas `mundo`/`mundos`; lo
   que trajo cartera nunca llega al proveedor de mercado. Las marcas no viajan.
   Los mundos se relacionan por el foco: cada uno declara qué claves lee
   (`Agente.foco`), y el código las aprende desde las herramientas.
4. **Con herramientas no viaja esquema** (OpenAI exige tools `strict`). La
   respuesta cierra con `Falta: …`; `esquema.leer` entiende las dos formas.
5. **Reglas antes que modelo.** Una regla se lee en una línea. El evento
   `despacho` dice siempre quién decidió.
6. **Las conversaciones tienen dueño** (`ia.conversaciones`, filtradas por
   email en cada query). El navegador manda pregunta + sesión, nada más.
7. **Una tarea = un agente = un modelo**, declarada en `core/modelos.TAREAS`;
   `datos: "negocio"` solo a proveedores que no entrenan (o con el flag);
   `datos: "personal"` nunca a quien entrena y sin texto en la traza.

## Antes de pushear

`python -m pytest -q tests/unit/test_asistente.py` · `ruff check .` ·
`python -c "import api.main"` · si se tocó un endpoint, `gen_mapa_app --check`.
No hay claves de proveedor en el entorno de Claude: lo que depende del
proveedor real se prueba en el LAB, y se dice.
