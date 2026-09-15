"""El RUTEO: a qué agentes les toca una pregunta. Tres capas, de la más barata
a la más cara: las REGLAS (código, sin costo, trazables), el modelo si ninguna
decide, y todos los candidatos si el modelo no se entiende.

No es un agente: no tiene herramientas ni bucle. Es una función que devuelve
una `Decision`. Doc: docs/AvAgentAI.md §6."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from asistente.agentes import AGENTES, FAMILIAS, presentacion

# Con qué modelo corre la capa 2 (`core/modelos.TAREAS`).
TAREA = "asistente_ruteo"


@dataclass(frozen=True)
class Decision:
    """Qué hacer con la pregunta. `tipo` dice cómo leer el resto: no se deduce
    de qué campo vino lleno."""

    # "van" · "contesta" · "elige_el_modelo"
    tipo: str
    motivo: str
    # "van": los que corren. "elige_el_modelo": entre quiénes. "contesta": vacío.
    agentes: tuple[str, ...] = ()
    respuesta: str | None = None

    @classmethod
    def van(cls, agentes: Iterable[str], motivo: str) -> Decision:
        return cls("van", motivo, tuple(agentes))

    @classmethod
    def contesta(cls, respuesta: str, motivo: str) -> Decision:
        return cls("contesta", motivo, respuesta=respuesta)

    @classmethod
    def elige_el_modelo(cls, candidatos: Iterable[str], motivo: str) -> Decision:
        return cls("elige_el_modelo", motivo, tuple(candidatos))


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin espacios de más. Todo lo demás compara sobre esto."""
    plano = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(plano.lower().split())


def _vistas(pregunta: str, senales: Iterable[str]) -> list[str]:
    """Las señales que aparecen en la pregunta como PALABRA ENTERA: «cer» no es
    «cerca» ni «cerrar». Las variantes de una palabra se declaran, no se infieren."""
    return [s for s in senales if re.search(rf"\b{re.escape(s)}\b", pregunta)]


def senales_en(pregunta: str) -> dict[str, list[str]]:
    """agente → sus señales propias que aparecen en la pregunta."""
    return {n: v for n, a in AGENTES.items() if (v := _vistas(pregunta, a.senales))}


def familias_en(pregunta: str) -> dict[str, list[str]]:
    """familia → sus señales genéricas que aparecen en la pregunta."""
    return {n: v for n, f in FAMILIAS.items() if (v := _vistas(pregunta, f.senales))}


# ── capa 1: las reglas ──────────────────────────────────────────────────────
#
# Una regla recibe la pregunta normalizada y devuelve una `Decision` o None. Se
# prueban en orden; la primera que decide, decide. Una regla se lee en una
# línea o no es una regla: es un modelo escrito a mano.

# Una pregunta META es UNA de estas frases, con un saludo adelante como mucho.
# No una frase que las contenga: «necesito ayuda con la 805» no es meta.
META = ("que sabes hacer", "que podes hacer", "que haces", "que sos", "ayuda",
        "que herramientas tenes", "como te uso", "para que servis")
_META_RE = re.compile(r"(?:hola|buenas|buen dia)?[\s,]*(" + "|".join(map(re.escape, META)) + r")(?:\s+vos)?")


def regla_meta(pregunta: str) -> Decision | None:
    """«¿Qué sabés hacer?» se contesta desde el registro, sin modelo ni agentes."""
    limpia = re.sub(r"[^\w\s]", " ", pregunta).strip()
    if _META_RE.fullmatch(limpia):
        return Decision.contesta(presentacion(), "regla: meta")
    return None


def regla_senales(pregunta: str) -> Decision | None:
    """Las señales propias nombran a uno o más agentes: van esos. Si solo hay
    genéricas de UNA familia («cuánto rinde»), es de esa familia y cuál de sus
    agentes lo elige el modelo, entre ellos y nadie más."""
    if vistas := senales_en(pregunta):
        agentes = tuple(n for n in AGENTES if n in vistas)
        detalle = "; ".join(f"{n}: {', '.join(vistas[n])}" for n in agentes)
        return Decision.van(agentes, f"regla: señales ({detalle})")
    genericas = familias_en(pregunta)
    if len(genericas) == 1:
        familia, palabras = next(iter(genericas.items()))
        candidatos = [n for n, a in AGENTES.items() if a.familia == familia]
        return Decision.elige_el_modelo(
            candidatos, f"regla: familia {familia} ({', '.join(palabras)})")
    return None


REGLAS: tuple[Callable[[str], Decision | None], ...] = (regla_meta, regla_senales)


def por_reglas(pregunta: str) -> Decision | None:
    """La primera regla que decide, o None si ninguna."""
    p = normalizar(pregunta)
    for regla in REGLAS:
        if (d := regla(p)) is not None:
            return d
    return None


# ── capa 2: el modelo, entre los candidatos ─────────────────────────────────


def instruccion(foco: dict, candidatos: Iterable[str] | None = None) -> str:
    """El SYSTEM de la capa 2. Solo los candidatos son elegibles; el foco entra
    porque una pregunta corta («¿y en dólares?») sigue sobre lo anterior."""
    elegibles = {n: a for n, a in AGENTES.items() if candidatos is None or n in candidatos}
    grupos: dict[str, list] = {}
    for a in elegibles.values():
        grupos.setdefault(a.familia, []).append(a)
    lineas = "\n".join(
        (f"  [{familia}]\n" if familia else "") + "\n".join(f"  {a.nombre}: {a.describe}" for a in ags)
        for familia, ags in grupos.items())
    en_foco = ""
    if foco:
        pares = ", ".join(f"{k} = {v}" for k, v in foco.items())
        en_foco = (f"La conversación viene hablando de: {pares}. Una pregunta corta "
                   f"(«¿y en dólares?») sigue sobre eso.\n")
    return (
        "Decidís qué agentes hacen falta para contestar una pregunta. No la contestás.\n"
        f"Agentes:\n{lineas}\n{en_foco}"
        f"Contestá SOLO los nombres de los agentes que hacen falta, separados por coma, "
        f"de esta lista: {', '.join(elegibles)}. Si la pregunta cruza dos agentes, van los dos.\n"
    )


def leer_eleccion(texto: str, candidatos: Iterable[str] | None = None) -> list[str]:
    """Los agentes que nombró el modelo. Solo se acepta una lista de nombres:
    una frase no se interpreta. Vacío = no se entendió (van todos los candidatos)."""
    t = normalizar(texto)
    elegibles = [n for n in AGENTES if candidatos is None or n in candidatos]
    nombres = "|".join(map(re.escape, elegibles))
    if not re.fullmatch(rf"({nombres})(\s*[,y]\s*({nombres}))*\.?", t):
        return []
    return [m for m in elegibles if re.search(rf"\b{m}\b", t)]
