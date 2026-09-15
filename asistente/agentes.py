"""Los agentes del asistente, declarados como objetos. Arquitectura: docs/AvAgentAI.md."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from asistente import esquema as ESQ
from asistente import herramientas as H


@dataclass(frozen=True)
class Agente:
    """Un agente es una tarea de ruteo, una instrucción y un conjunto de herramientas."""

    nombre: str
    # Clave en `core/modelos.TAREAS`: decide proveedor, modelo y si ve datos del negocio.
    tarea: str
    # Una línea que el despacho lee para decidir si este mundo atiende la pregunta.
    describe: str
    # Devuelve el SYSTEM a partir del foco de la conversación.
    instruccion: Callable[[dict], str]
    herramientas: tuple[Callable, ...] = ()
    # Solo el mundo que ve cuentas aprende y lee la cuenta en foco.
    aprende_foco: bool = False

    @property
    def por_nombre(self) -> dict[str, Callable]:
        return {f.__name__: f for f in self.herramientas}


_COMUN = """\
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
""" + ESQ.INSTRUCCION + "\n"

_CUENTA = """
Si el usuario ya nombró una cuenta —en esta pregunta o antes: la que está en
foco—, usala. Si no hay ninguna nombrada ni en foco y hay más de una
habilitada, preguntale cuál quiere: no elijas vos.
"""

_MERCADO = """
Hablás del MERCADO: lo que hay y cuánto rinde, sin importar quién lo tenga.
Una tasa marcada `tasa_ruido` no es comparable: no la uses para decir cuál
rinde más. Un ticker que no existe se dice, no se reemplaza por uno parecido.
"""


def _instruccion_cuenta(foco: dict) -> str:
    from asistente import estado as EST

    cuentas = H.cuentas_disponibles().get("cuentas") or []
    partes = [_COMUN, _CUENTA]
    if cuentas:
        lista = "\n".join(f"  {c['id_cuenta']} — {c['nombre']}" for c in cuentas)
        partes.append("Cuentas habilitadas (son las ÚNICAS que podés consultar; el número "
                      f"es el `cuenta` que llevan las herramientas):\n{lista}\n")
    partes.append(EST.como_texto(foco))
    return "".join(partes)


def _instruccion_mercado(_foco: dict) -> str:
    return _COMUN + _MERCADO


CUENTA = Agente(
    nombre="cuenta",
    tarea="asistente_cuenta",
    describe="la plata y las tenencias de UNA cuenta de cliente: qué tiene, cuánto cobra, "
             "qué le vence. Cualquier pregunta que hable de una cuenta, de «lo mío» o de "
             "«lo que tengo».",
    instruccion=_instruccion_cuenta,
    herramientas=H.DE_LA_CUENTA,
    aprende_foco=True,
)

MERCADO = Agente(
    nombre="mercado",
    tarea="asistente_mercado",
    describe="el mercado, sin importar quién lo tenga: qué bonos hay en una curva, cuánto "
             "rinden, qué es un bono, cuándo vence, cómo cotiza.",
    instruccion=_instruccion_mercado,
    herramientas=H.DEL_MERCADO,
)

# Los mundos, por nombre. Sumar uno es declararlo acá y en `core/modelos.TAREAS`.
MUNDOS: dict[str, Agente] = {a.nombre: a for a in (CUENTA, MERCADO)}


def _instruccion_despacho(_foco: dict) -> str:
    lineas = "\n".join(f"  {n}: {a.describe}" for n, a in MUNDOS.items())
    nombres = ", ".join(MUNDOS)
    return (
        "Decidís qué mundos hacen falta para contestar una pregunta. No la contestás.\n"
        f"Mundos:\n{lineas}\n"
        f"Contestá SOLO los nombres de los mundos que hacen falta, separados por coma, "
        f"de esta lista: {nombres}. Si la pregunta compara lo de una cuenta con el "
        f"mercado, van los dos.\n"
    )


DESPACHO = Agente(
    nombre="despacho",
    tarea="asistente_despacho",
    describe="decide qué mundos atienden la pregunta",
    instruccion=_instruccion_despacho,
)


def _instruccion_junta(_foco: dict) -> str:
    return (
        _COMUN
        + "\nTe llegan la pregunta y lo que contestó cada mundo, con los datos que usó. "
          "Escribí UNA respuesta que cruce los dos. Todo número que escribas tiene que "
          "estar en esos datos.\n"
    )


# La junta redacta con los datos de varios mundos. Corre con la tarea de CUENTA
# porque ve datos del negocio.
JUNTA = Agente(
    nombre="junta",
    tarea=CUENTA.tarea,
    describe="cruza lo que contestaron varios mundos",
    instruccion=_instruccion_junta,
)
# Lo que escribe la junta queda en el historial como de este mundo: es el que
# ve datos del negocio, y es el único que puede releerlo.
JUNTA_COMO = CUENTA.nombre
