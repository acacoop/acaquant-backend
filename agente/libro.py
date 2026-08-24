"""`agente/libro.py` — lo que el agente ESCRIBIÓ. Dibuja HISTORIAL.

Es la puerta única del LIBRO, igual que `registro` lo es de los hallazgos. Se
llama `libro` y no `acciones` a propósito: `agente/arreglos.py` es el que
APLICA, y dos módulos con el mismo nombre en el mismo subsistema se confunden
en cualquier revisión.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging

from agente import registro

logger = logging.getLogger(__name__)

# ⚠️ **EL CONTEXTO DE QUIÉN ESTÁ ESCRIBIENDO.**
#
# La cadena de alta anota sus propias líneas (`alta_bono`, `sembrar_especies`,
# `sembrar_tasa_1816`) y no conoce el hallazgo que la disparó, así que salían
# con `? · ?` en el trío — justo el campo que hace que HISTORIAL pueda contestar
# «¿quedó arreglado?».
#
# Pasarle el trío a mano a las 14 llamadas de `alta.py` sería pedirle a cada una
# que se acuerde. Se resuelve donde SÍ se sabe: el arreglo abre el contexto y
# todo lo que se anote adentro lo hereda.
_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "libro_ctx", default=None)
_escritas: contextvars.ContextVar[int] = contextvars.ContextVar("libro_n", default=0)


@contextlib.contextmanager
def contexto(*, habilidad: str, sujeto: str, regla: str,
             hallazgo_id: int | None = None, por: str = ""):
    """Todo lo que se anote acá adentro hereda el trío del hallazgo."""
    tok = _ctx.set({"habilidad": habilidad, "sujeto": sujeto, "regla": regla,
                    "hallazgo_id": hallazgo_id, "por": por})
    tok_n = _escritas.set(0)
    try:
        yield lambda: _escritas.get()
    finally:
        _ctx.reset(tok)
        _escritas.reset(tok_n)


def registrar(*, accion: str = "", objetivo: str = "", habilidad: str = "",
              regla: str = "", hallazgo_id: int | None = None, por: str = "",
              destino: str = "", campo: str = "", antes="", despues="",
              ok: bool = True, error: str = "", **_ignorado) -> None:
    """Una línea del libro. **Nunca levanta**: un problema anotando no puede
    tirar abajo la escritura que ya se hizo."""
    ctx = _ctx.get() or {}
    try:
        registro.anotar_accion(
            arreglo=accion or "?",
            habilidad=habilidad or ctx.get("habilidad") or "?",
            sujeto=objetivo or ctx.get("sujeto") or "?",
            regla=regla or ctx.get("regla") or "?",
            hallazgo_id=hallazgo_id or ctx.get("hallazgo_id"),
            por=por or ctx.get("por") or "",
            donde=destino, campo=campo,
            antes="" if antes is None else str(antes),
            despues="" if despues is None else str(despues),
            ok=ok, error=error)
        _escritas.set(_escritas.get() + 1)
    except Exception as e:
        logger.warning("agente/libro: no pude anotar %s/%s (%s)",
                       accion, objetivo, e)
