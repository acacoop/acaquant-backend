"""La junta: cuando varios agentes contestaron, una llamada más redacta con los
datos de todos delante. Corre con la tarea de CARTERA porque ve datos del
negocio, y lo que escribe queda en el historial como de ese agente."""
from __future__ import annotations

from asistente.agente import COMUN, Agente
from asistente.agentes import cartera


def _instruccion(_foco: dict) -> str:
    return (
        COMUN
        + "\nTe llegan la pregunta y lo que contestó cada agente, con los datos que usó. "
          "Escribí UNA respuesta que cruce los dos. Todo número que escribas tiene que "
          "estar en esos datos.\n"
    )


JUNTA = Agente(
    nombre="junta",
    tarea=cartera.AGENTE.tarea,
    describe="cruza lo que contestaron varios agentes",
    instruccion=_instruccion,
)
# Con qué marca queda en el historial: el agente que ve datos del negocio es el
# único que puede releer lo que escribió la junta.
JUNTA_COMO = cartera.AGENTE.nombre
