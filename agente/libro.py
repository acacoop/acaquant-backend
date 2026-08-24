"""`agente/libro.py` — lo que el agente ESCRIBIÓ. Dibuja HISTORIAL.

Es la puerta única del LIBRO, igual que `registro` lo es de los hallazgos. Se
llama `libro` y no `acciones` a propósito: `agente/arreglos.py` es el que
APLICA, y dos módulos con el mismo nombre en el mismo subsistema se confunden
en cualquier revisión.
"""
from __future__ import annotations

import logging

from agente import registro

logger = logging.getLogger(__name__)


def registrar(*, accion: str = "", objetivo: str = "", habilidad: str = "",
              regla: str = "", hallazgo_id: int | None = None, por: str = "",
              destino: str = "", campo: str = "", antes="", despues="",
              ok: bool = True, error: str = "", **_ignorado) -> None:
    """Una línea del libro. **Nunca levanta**: un problema anotando no puede
    tirar abajo la escritura que ya se hizo."""
    try:
        registro.anotar_accion(
            arreglo=accion or "?", habilidad=habilidad or "?",
            sujeto=objetivo or "?", regla=regla or "?",
            hallazgo_id=hallazgo_id, por=por, donde=destino, campo=campo,
            antes="" if antes is None else str(antes),
            despues="" if despues is None else str(despues),
            ok=ok, error=error)
    except Exception as e:
        logger.warning("agente/libro: no pude anotar %s/%s (%s)",
                       accion, objetivo, e)
