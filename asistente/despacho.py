"""El despacho: qué agentes atienden una pregunta. Primero las REGLAS (código,
sin costo, trazables); si ninguna decide, el modelo `asistente_despacho`; si
el modelo no se entiende, van todos. Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from asistente.agente import Agente
from asistente.agentes import AGENTES, FAMILIAS


@dataclass(frozen=True)
class Decision:
    # Los agentes que van. Vacío con `respuesta` = una regla contestó; vacío con
    # `entre` = el modelo elige, pero solo entre esos.
    agentes: tuple[str, ...]
    motivo: str
    respuesta: str | None = None
    entre: tuple[str, ...] = ()


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
    """agente → las señales suyas que aparecen en la pregunta, como palabra
    entera: «cer» no es «cerca» ni «cerrar»; las variantes se declaran."""
    out: dict[str, list[str]] = {}
    for nombre, a in AGENTES.items():
        vistas = [s for s in a.senales if re.search(rf"\b{re.escape(s)}\b", pregunta)]
        if vistas:
            out[nombre] = vistas
    return out


def familias_en(pregunta: str) -> dict[str, list[str]]:
    """familia → sus señales genéricas que aparecen en la pregunta."""
    out: dict[str, list[str]] = {}
    for nombre, f in FAMILIAS.items():
        vistas = [s for s in f.senales if re.search(rf"\b{re.escape(s)}\b", pregunta)]
        if vistas:
            out[nombre] = vistas
    return out


def regla_meta(pregunta: str, _foco: dict) -> Decision | None:
    """«¿Qué sabés hacer?» se contesta desde los objetos, sin modelo ni agentes."""
    limpia = re.sub(r"[^\w\s]", " ", pregunta).strip()
    if _META_RE.fullmatch(limpia):
        return Decision((), "regla: meta", respuesta=presentacion())
    return None


def regla_senales(pregunta: str, _foco: dict) -> Decision | None:
    """Las señales nombran a uno o más agentes: van esos, en el orden del
    registro. Una cuenta nombrada o en foco sin ninguna señal no decide nada:
    tres agentes la atienden y elegir es del modelo."""
    vistas = senales_en(pregunta)
    if vistas:
        agentes = tuple(n for n in AGENTES if n in vistas)
        detalle = "; ".join(f"{n}: {', '.join(vistas[n])}" for n in agentes)
        return Decision(agentes, f"regla: señales ({detalle})")
    # Solo señales genéricas de una familia («cuánto rinde», «cómo cotiza»):
    # es de la familia, y cuál de sus agentes lo decide el modelo entre ellos.
    genericas = familias_en(pregunta)
    if len(genericas) == 1:
        familia = next(iter(genericas))
        entre = tuple(n for n, a in AGENTES.items() if a.familia == familia)
        return Decision((), f"regla: familia {familia} ({', '.join(genericas[familia])}); elige el modelo",
                        entre=entre)
    return None


REGLAS: tuple[Callable[[str, dict], Decision | None], ...] = (regla_meta, regla_senales)


def por_reglas(pregunta: str, foco: dict | None) -> Decision | None:
    p = normalizar(pregunta)
    for regla in REGLAS:
        if (d := regla(p, foco or {})) is not None:
            return d
    return None


def presentacion() -> str:
    """Qué sabe hacer el asistente, escrito desde los objetos: nunca queda viejo."""
    lineas = ["Puedo mirar estas cosas, siempre con datos de la plataforma:"]
    for familia, agentes in por_familia().items():
        if familia:
            lineas.append(f"{familia}:")
        for a in agentes:
            herramientas = ", ".join(f.__name__ for f in a.herramientas) or "todavía ninguna"
            sangria = "  " if familia else ""
            lineas.append(f"{sangria}- {a.nombre}: {a.describe} Herramientas: {herramientas}.")
    lineas.append("No escribo nada ni invento datos: si una herramienta no lo trae, no lo digo.")
    return "\n".join(lineas)


# ── el agente que decide cuando las reglas no ───────────────────────────────


def por_familia() -> dict[str, list[Agente]]:
    """Los agentes agrupados por familia, en el orden del registro. Los que no
    tienen familia van bajo la clave vacía, primero."""
    out: dict[str, list[Agente]] = {"": []}
    for a in AGENTES.values():
        out.setdefault(a.familia, []).append(a)
    return {k: v for k, v in out.items() if v}


def instruccion(foco: dict, entre: tuple[str, ...] = ()) -> str:
    """El SYSTEM del despacho. Con `entre`, solo esos agentes son candidatos."""
    candidatos = {n: a for n, a in AGENTES.items() if not entre or n in entre}
    grupos: dict[str, list[Agente]] = {}
    for a in candidatos.values():
        grupos.setdefault(a.familia, []).append(a)
    lineas = "\n".join(
        (f"  [{familia}]\n" if familia else "") + "\n".join(f"  {a.nombre}: {a.describe}" for a in ags)
        for familia, ags in grupos.items())
    nombres = ", ".join(candidatos)
    en_foco = ""
    if foco:
        pares = ", ".join(f"{k} = {v}" for k, v in foco.items())
        en_foco = (f"La conversación viene hablando de: {pares}. Una pregunta corta "
                   f"(«¿y en dólares?») sigue sobre eso.\n")
    return (
        "Decidís qué agentes hacen falta para contestar una pregunta. No la contestás.\n"
        f"Agentes:\n{lineas}\n{en_foco}"
        f"Contestá SOLO los nombres de los agentes que hacen falta, separados por coma, "
        f"de esta lista: {nombres}. Si la pregunta cruza dos agentes, van los dos.\n"
    )


DESPACHO = Agente(
    nombre="despacho",
    tarea="asistente_despacho",
    describe="decide qué agentes atienden la pregunta",
    instruccion=lambda foco: instruccion(foco),
)


def leer_eleccion(texto: str, entre: tuple[str, ...] = ()) -> list[str]:
    """Los agentes que nombró el modelo, de entre los candidatos. Solo se
    acepta una lista de nombres: una frase no se interpreta (vacío = van todos
    los candidatos)."""
    t = normalizar(texto)
    candidatos = [n for n in AGENTES if not entre or n in entre]
    nombres = "|".join(map(re.escape, candidatos))
    if not re.fullmatch(rf"({nombres})(\s*[,y]\s*({nombres}))*\.?", t):
        return []
    return [m for m in candidatos if re.search(rf"\b{m}\b", t)]
