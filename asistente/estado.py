"""El foco de la conversación: lo que se SABE, aparte de lo que se DIJO. Un
dict chico que viaja con el historial pero aparte de él. Lo escribe el código
desde los argumentos de una herramienta que contestó; lo lee la instrucción
de los mundos que la declaran en `Agente.foco`. Cada clave tiene su lista
cerrada de valores válidos."""
from __future__ import annotations

from collections.abc import Callable

from asistente import permitido

# clave → función que devuelve los valores válidos. Lo que llega del navegador
# y no está acá, no existe.
EN_FOCO: dict[str, Callable[[], list[str]]] = {
    "cuenta": lambda: permitido.cuentas(),
}


def sanear(estado: dict | None) -> dict[str, str]:
    """Lo que llega de afuera, reducido a lo declarado."""
    if not isinstance(estado, dict):
        return {}
    out: dict[str, str] = {}
    for k, validos in EN_FOCO.items():
        v = estado.get(k)
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            s = str(v).strip()
            if s and s in validos():
                out[k] = s
    return out


def aprender(estado: dict[str, str], args: dict | None, resultado,
             claves: tuple[str, ...] | None = None) -> dict[str, str]:
    """El foco después de que una herramienta corrió con `args`. Un resultado
    con `error` no enseña nada. `claves` acota a las que el mundo declara.
    Devuelve un dict nuevo."""
    if not isinstance(args, dict):
        return dict(estado)
    if isinstance(resultado, dict) and resultado.get("error"):
        return dict(estado)
    mirar = [k for k in EN_FOCO if k in args and (claves is None or k in claves)]
    delta = sanear({k: args[k] for k in mirar})
    return {**estado, **delta}


def como_texto(estado: dict[str, str]) -> str:
    """El renglón que va al final de la instrucción. Solo el dato; la regla de
    qué hacer con él vive en la instrucción."""
    if not estado:
        return ""
    pares = ", ".join(f"{k} = {v}" for k, v in estado.items())
    return f"En foco por las preguntas anteriores: {pares}.\n"
