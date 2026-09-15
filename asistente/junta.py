"""La JUNTA: cuando contestaron varios agentes, una llamada más redacta con los
datos de todos delante. No es un agente: no tiene herramientas ni bucle.

Corre con la tarea de CARTERA porque ve datos del negocio, y lo que escribe
queda en el historial marcado como de ese agente: es el único que puede
releerlo. Doc: docs/AvAgentAI.md §2."""
from __future__ import annotations

from asistente.agente import COMUN
from asistente.agentes import cartera

TAREA = cartera.AGENTE.tarea
# Con qué marca queda en el historial lo que escribe.
COMO = cartera.AGENTE.nombre


def instruccion(_foco: dict) -> str:
    return (
        COMUN
        + "\nTe llegan la pregunta y lo que contestó cada agente, con los datos que usó. "
          "Escribí UNA respuesta que los cruce. Todo número que escribas tiene que "
          "estar en esos datos.\n"
    )
