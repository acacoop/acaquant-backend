"""Qué es un agente del asistente. Un objeto: tarea de ruteo, instrucción,
herramientas, señales para el despacho. Los declarados viven en `mundos/`
(uno por archivo), `despacho.py` y `junta.py`. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from asistente import esquema as ESQ


@dataclass(frozen=True)
class Agente:
    nombre: str
    # Clave en `core/modelos.TAREAS`: decide proveedor, modelo y si ve datos del negocio.
    tarea: str
    # Una línea que el despacho lee para decidir si este mundo atiende la pregunta.
    describe: str
    # Devuelve el SYSTEM a partir del foco de la conversación.
    instruccion: Callable[[dict], str]
    herramientas: tuple[Callable, ...] = ()
    # Palabras enteras (sin acentos, minúsculas; las variantes se declaran) que
    # delatan que la pregunta es de este mundo. Las lee `despacho.por_reglas`:
    # si alcanzan, no se llama al modelo.
    senales: tuple[str, ...] = ()
    # Claves del foco que este mundo lee y aprende (`estado.EN_FOCO`). Varios
    # mundos pueden compartir una: es lo que los relaciona entre preguntas.
    foco: tuple[str, ...] = ()

    @property
    def por_nombre(self) -> dict[str, Callable]:
        return {f.__name__: f for f in self.herramientas}


# Lo que todo agente que redacta tiene delante. Cada mundo agrega lo suyo.
COMUN = """\
Sos el asistente de una mesa de renta fija argentina. Te habla el admin de la
plataforma. Contestás en castellano, corto y concreto.

TODO dato sale de las herramientas. Nunca inventes un ticker, una fecha, un
nominal ni una tasa: si no lo trajo una herramienta, no lo digas.

Si una herramienta devuelve un `error`, decilo con sus palabras y no lo tapes
con una estimación. «No pude mirar» es una respuesta válida.

Si un resultado trae la fecha de los datos, decila: los números son de esa
foto, no de este momento. Si algo quedó afuera, nombralo; si no quedó nada
afuera, no lo menciones.

No conviertas, redondees ni sumes números por tu cuenta. Reportá los que
devolvió la herramienta, con su moneda. Si todo el resultado está en UNA sola
moneda, decila una vez y no en cada renglón.

Cuando enumeres, un renglón corto por ítem. No repitas en cada renglón lo que
ya dijiste arriba. Si un resultado trae una tabla, la dibuja la pantalla: no
la enumeres vos. Contestá en una o dos frases y dejá que la tabla hable.

Texto plano: sin asteriscos, sin títulos, sin tablas con barras. La pantalla
no dibuja markdown.
""" + ESQ.INSTRUCCION + "\n"


def sistema(agente: Agente, foco: dict) -> str:
    """El SYSTEM completo de un agente: su instrucción más la fecha de hoy. Va
    al final para no romper el caché del prefijo. Sin esto, «hasta fin de
    año» se calcula desde una fecha que el modelo inventa."""
    return agente.instruccion(foco) + f"\nHoy es {date.today().isoformat()}.\n"
