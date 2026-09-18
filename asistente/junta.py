"""La JUNTA: cuando contestaron varios agentes, una llamada más redacta con los
datos de todos delante. No es un agente: no tiene herramientas ni bucle.

Usa la protección de traza más restrictiva de los agentes participantes. Doc:
docs/AvAgentAI.md §2."""
from __future__ import annotations

from asistente.agente import COMUN
from asistente.agentes import AGENTES, cartera
from core import modelos

TAREA = cartera.AGENTE.tarea
TAREA_PERSONAL = "asistente_junta_personal"


def tarea(agentes: list[str]) -> str:
    if any(modelos.resolver(AGENTES[nombre].tarea).traza_sin_texto for nombre in agentes):
        return TAREA_PERSONAL
    return TAREA


def como(agentes: list[str]) -> str:
    """La respuesta sensible sólo puede releerla otro agente con traza protegida."""
    protegidos = [nombre for nombre in agentes
                  if modelos.resolver(AGENTES[nombre].tarea).traza_sin_texto]
    return protegidos[0] if protegidos else cartera.AGENTE.nombre


def instruccion(_foco: dict) -> str:
    return (
        COMUN
        + "\nTe llegan la pregunta y lo que contestó cada agente, con los datos que usó. "
          "Escribí UNA respuesta que los cruce. Todo número que escribas tiene que "
          "estar en esos datos.\n"
    )
