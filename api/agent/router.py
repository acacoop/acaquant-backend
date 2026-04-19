"""Router heurístico: decide si una pregunta del usuario va a Haiku o Sonnet.

Haiku (default): lookups simples, cotizaciones, saludos, consultas directas.
Sonnet: análisis estratégico, comparaciones, recomendaciones, multi-paso.

Se hace por keywords en el mensaje + longitud. Si matchea algún trigger
estratégico → Sonnet. Si no → Haiku.
"""
from __future__ import annotations

import re

# Triggers que indican pregunta analítica/estratégica → Sonnet.
# Matcheo case-insensitive sobre el mensaje completo.
SONNET_TRIGGERS = [
    # Comparaciones
    r"\b(comparame|compará|compar(a|á) )",
    r"\b(vs|versus)\b",
    r"\b(o)\s+(lecap|cer|tasa[- ]fija|hd|dl|boncer|bonar)\b",
    r"\b(cer|lecap|hd|dl|boncer|bonar)\s+o\b",

    # Recomendaciones / view
    r"\b(recomend(a|á|ás|arias|aría)|sugerí|sugerís|qué hago)",
    r"\b(qué opinás?|qué pensás?|qué te parece|cómo lo ves)",
    r"\b(cómo ves|cómo viene|view|tesis|análisis|analizame|analiz(á|a))",
    r"\b(qué rotar|qué rot(ar|o)|rotación)",
    r"\b(armá?me|armar?me|construí|armar un|estructura)",
    r"\b(estirar|acortarse|duration)\b",

    # Preguntas abiertas / resúmenes
    r"\b(resumen|resumí|síntesis|conclusión|cuadro de situación)\b",
    r"\b(qué pasó|cómo cerró|qué está pasando)\b",

    # Estructuras de opciones / renta fija técnicas
    r"\b(covered call|protective put|bull spread|bear spread|butterfly|condor|iron condor|straddle|strangle|ratio backspread|collar|calendar spread|diagonal spread)\b",
    r"\b(barbell|bullet|ladder|steepener|flattener|immunization|carry|roll-down|roll down|break[- ]?even|forward\s+implí?cit|arbitraje|arb|tips[- ]treasury)\b",

    # Framework
    r"\b(régimen|programa financiero|riesgo político|economía real|valor relativo)\b",
    r"\b(escenario|asimetr(ia|í))\b",

    # Brechas / macro
    r"\b(brecha|canje|mep vs|ccl vs|rollover|bid[- ]to[- ]cover|licitación)\b",
]

# Pre-compilamos para velocidad.
_SONNET_RE = [re.compile(p, re.IGNORECASE) for p in SONNET_TRIGGERS]


def decide_model(user_message: str, *, force: str | None = None) -> str:
    """Decide entre 'haiku' y 'sonnet' para el turno actual.

    Args:
        user_message: pregunta del usuario en texto plano.
        force: si se pasa 'haiku' o 'sonnet', se respeta y se ignoran heurísticas.

    Returns:
        'haiku' o 'sonnet'.
    """
    if force in ("haiku", "sonnet"):
        return force

    text = (user_message or "").strip()
    if not text:
        return "haiku"

    # Mensaje muy largo (elaborado) → probablemente analítico
    if len(text) > 280:
        return "sonnet"

    # Matchea algún trigger estratégico → Sonnet
    for regex in _SONNET_RE:
        if regex.search(text):
            return "sonnet"

    # Todo lo demás → Haiku
    return "haiku"
