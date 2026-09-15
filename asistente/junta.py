"""La junta: cuando varios mundos contestaron, una llamada más redacta con los
datos de todos delante. Corre con la tarea de CUENTA porque ve datos del
negocio, y lo que escribe queda en el historial como de ese mundo."""
from __future__ import annotations

from asistente.agente import COMUN, Agente
from asistente.mundos import cuenta


def _instruccion(_foco: dict) -> str:
    return (
        COMUN
        + "\nTe llegan la pregunta y lo que contestó cada mundo, con los datos que usó. "
          "Escribí UNA respuesta que cruce los dos. Todo número que escribas tiene que "
          "estar en esos datos.\n"
    )


JUNTA = Agente(
    nombre="junta",
    tarea=cuenta.AGENTE.tarea,
    describe="cruza lo que contestaron varios mundos",
    instruccion=_instruccion,
)
# Con qué marca queda en el historial: el mundo que ve datos del negocio es el
# único que puede releer lo que escribió la junta.
JUNTA_COMO = cuenta.AGENTE.nombre
