"""El foco de la conversación: lo que se SABE, aparte de lo que se DIJO. Un
dict chico que viaja con el historial pero aparte de él. Lo escribe el código
desde los argumentos de una herramienta que contestó; lo lee la instrucción
de los agentes que la declaran en `Agente.foco`. Cada clave tiene su lista
cerrada de valores válidos."""
from __future__ import annotations

import re
from collections.abc import Callable

from asistente import permitido

# Un ticker corto del master: mayúsculas y dígitos, de 2 a 10 (AL30, S31O5,
# YPFD). Es forma, no existencia: la existencia la dice la herramienta.
_TICKER_RE = re.compile(r"[A-Z0-9]{2,10}")

# clave → (normalizar, es válido). Lo que llega de afuera y no pasa, no existe.
EN_FOCO: dict[str, tuple[Callable[[str], str], Callable[[str], bool]]] = {
    "cuenta": (str.strip, lambda v: v in permitido.cuentas()),
    "ticker": (lambda v: v.strip().upper(), lambda v: bool(_TICKER_RE.fullmatch(v))),
}


def sanear(estado: dict | None) -> dict[str, str]:
    """Lo que llega de afuera, reducido a lo declarado."""
    if not isinstance(estado, dict):
        return {}
    out: dict[str, str] = {}
    for k, (normalizar, valido) in EN_FOCO.items():
        v = estado.get(k)
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            s = normalizar(str(v))
            if s and valido(s):
                out[k] = s
    return out


def aprender(estado: dict[str, str], args: dict | None, resultado,
             claves: tuple[str, ...] | None = None) -> dict[str, str]:
    """El foco después de que una herramienta corrió con `args`. Un resultado
    con `error` no enseña nada. `claves` acota a las que el agente declara.
    Devuelve un dict nuevo."""
    if not isinstance(args, dict):
        return dict(estado)
    if isinstance(resultado, dict) and resultado.get("error"):
        return dict(estado)
    mirar = [k for k in EN_FOCO if k in args and (claves is None or k in claves)]
    delta = sanear({k: args[k] for k in mirar})
    return {**estado, **delta}


def como_texto(estado: dict[str, str], claves: tuple[str, ...] | None = None) -> str:
    """El renglón que va al final de la instrucción. Solo el dato; la regla de
    qué hacer con él vive en la instrucción."""
    visible = estado if claves is None else {k: estado[k] for k in claves if k in estado}
    if not visible:
        return ""
    pares = ", ".join(f"{k} = {v}" for k, v in visible.items())
    return f"En foco por las preguntas anteriores: {pares}.\n"
