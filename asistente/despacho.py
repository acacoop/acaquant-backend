"""El despacho: qué mundos atienden una pregunta. Primero las REGLAS (código,
sin costo, trazables); si ninguna decide, el modelo `asistente_despacho`; si
el modelo no se entiende, van todos. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from asistente import permitido
from asistente.agente import Agente
from asistente.mundos import MUNDOS


@dataclass(frozen=True)
class Decision:
    # Los mundos que van. Vacío = nadie: `respuesta` ya es la respuesta.
    mundos: tuple[str, ...]
    motivo: str
    respuesta: str | None = None


# ── reglas ──────────────────────────────────────────────────────────────────
#
# Cada regla recibe la pregunta normalizada (minúsculas, sin acentos) y el foco,
# y devuelve una `Decision` o None. Se prueban en orden; la primera que decide,
# decide. Una regla se lee en una línea o no es una regla: es un modelo escrito
# a mano.

# Una pregunta META es UNA de estas frases (con un saludo adelante como mucho),
# no una frase que las contenga: «necesito ayuda con la 805» es de cuenta.
META = ("que sabes hacer", "que podes hacer", "que haces", "que sos", "ayuda",
        "que herramientas tenes", "como te uso", "para que servis")
_META_RE = re.compile(r"(?:hola|buenas|buen dia)?[\s,]*(" + "|".join(map(re.escape, META)) + r")(?:\s+vos)?")


def normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(plano.lower().split())


def senales_en(pregunta: str) -> dict[str, list[str]]:
    """mundo → las señales suyas que aparecen en la pregunta, como palabra
    entera: «cer» no es «cerca» ni «cerrar»; las variantes se declaran."""
    out: dict[str, list[str]] = {}
    for nombre, a in MUNDOS.items():
        vistas = [s for s in a.senales if re.search(rf"\b{re.escape(s)}\b", pregunta)]
        if vistas:
            out[nombre] = vistas
    return out


def cuenta_nombrada(pregunta: str) -> str | None:
    """Una cuenta habilitada nombrada en la pregunta, como palabra entera."""
    for c in permitido.cuentas():
        if re.search(rf"\b{re.escape(str(c).lower())}\b", pregunta):
            return str(c)
    return None


def regla_meta(pregunta: str, _foco: dict) -> Decision | None:
    """«¿Qué sabés hacer?» se contesta desde los objetos, sin modelo ni mundos."""
    limpia = re.sub(r"[^\w\s]", " ", pregunta).strip()
    if _META_RE.fullmatch(limpia):
        return Decision((), "regla: meta", respuesta=presentacion())
    return None


def regla_foco(pregunta: str, foco: dict) -> Decision | None:
    """Hay cuenta (nombrada o en foco) y ninguna señal de otro mundo: va el
    mundo que aprende foco, solo."""
    de_foco = [n for n, a in MUNDOS.items() if a.aprende_foco]
    if not de_foco:
        return None
    if not (foco or cuenta_nombrada(pregunta)):
        return None
    otros = set(senales_en(pregunta)) - set(de_foco)
    if otros:
        return None
    return Decision(tuple(de_foco), "regla: cuenta en foco")


def regla_senales(pregunta: str, _foco: dict) -> Decision | None:
    """Las señales nombran a uno o más mundos: van esos, en el orden del
    registro. Una cuenta nombrada suma al mundo que la atiende aunque no haya
    otra palabra suya («compará la 805 con la curva CER» va a los dos)."""
    vistas = senales_en(pregunta)
    if (c := cuenta_nombrada(pregunta)) is not None:
        for n, a in MUNDOS.items():
            if a.aprende_foco:
                vistas.setdefault(n, []).append(f"cuenta {c}")
    if not vistas:
        return None
    mundos = tuple(n for n in MUNDOS if n in vistas)
    detalle = "; ".join(f"{n}: {', '.join(vistas[n])}" for n in mundos)
    return Decision(mundos, f"regla: señales ({detalle})")


REGLAS: tuple[Callable[[str, dict], Decision | None], ...] = (
    regla_meta, regla_foco, regla_senales,
)


def por_reglas(pregunta: str, foco: dict | None) -> Decision | None:
    p = normalizar(pregunta)
    for regla in REGLAS:
        if (d := regla(p, foco or {})) is not None:
            return d
    return None


def presentacion() -> str:
    """Qué sabe hacer el asistente, escrito desde los objetos: nunca queda viejo."""
    lineas = ["Puedo mirar estas cosas, siempre con datos de la plataforma:"]
    for a in MUNDOS.values():
        herramientas = ", ".join(f.__name__ for f in a.herramientas)
        lineas.append(f"- {a.nombre}: {a.describe} Herramientas: {herramientas}.")
    lineas.append("No escribo nada ni invento datos: si una herramienta no lo trae, no lo digo.")
    return "\n".join(lineas)


# ── el agente que decide cuando las reglas no ───────────────────────────────


def _instruccion(_foco: dict) -> str:
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
    instruccion=_instruccion,
)


def leer_eleccion(texto: str) -> list[str]:
    """Los mundos que nombró el modelo. Solo se acepta una lista de nombres:
    una frase no se interpreta (vacío = van todos)."""
    t = normalizar(texto)
    nombres = "|".join(map(re.escape, MUNDOS))
    if not re.fullmatch(rf"({nombres})(\s*[,y]\s*({nombres}))*\.?", t):
        return []
    return [m for m in MUNDOS if re.search(rf"\b{m}\b", t)]
