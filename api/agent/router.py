"""Router heurístico: decide si una pregunta del usuario va a Haiku o Sonnet.

DEFAULT = Sonnet. Haiku SOLO si la pregunta es claramente lookup puro corto
o saludo. Heurística inversa al diseño anterior, que tenía default Haiku +
triggers regex para Sonnet — ese diseño rompía en los bordes:
  - "qué pasaría con CER si la inflación se acelera" → no matcheaba triggers,
     iba a Haiku, daba respuesta pobre.
  - "explícame por qué la curva está invertida" → idem.
  - "el 26 vs el 28" → matcheaba `vs`, iba a Sonnet, podía haber sido lookup.

El costo subió (Sonnet ~10x Haiku), pero la calidad de las respuestas en los
bordes es más predecible. Si en el futuro se quiere abaratar más sin
sacrificar calidad, alternativa robusta es un primer pase con Haiku que
clasifique (`is_strategic`) y enrute en consecuencia.
"""
from __future__ import annotations

import re

# Patrones que disparan Haiku. Si NO matchea ninguno, va a Sonnet.
# Anclados al inicio del texto (^) para no matchear keywords sueltos en
# preguntas elaboradas (ej. "no me digas el precio, decime tu view" NO debe
# disparar Haiku por la palabra "precio").
HAIKU_PATTERNS = [
    # Saludos / smalltalk
    r"^(hola|buen[oa]s?\b|gracias|ok\b|listo|perfecto|dale|bárbaro|chau|adi[oó]s|hi\b)",
    # Lookup puro: pregunta arranca con campo o término de cotización.
    r"^(precio|cotización|cotizacion|vto|vencimiento|cupón|cupon|paridad|tem|tea|duration)\b",
    # "cómo está X" / "cómo viene X" / "cuánto vale X" — info directa de UN activo.
    r"^(cómo|como)\s+(está|viene|vale|cierra|cerró|cotiza)\s+\S+",
    r"^(cuánto|cuanto)\s+(vale|cuesta|paga|rinde|cotiza)",
    r"^(qué|que)\s+(precio|paridad|tea|tem|duration|cupón|cupon|vto|vence)\s+",
    # Mensaje que es solo un ticker (o ticker + ?). El usuario pide info implícita.
    r"^[A-Z]{1,4}\d{1,4}[A-Z]?\d?\??$",
]

_HAIKU_RE = [re.compile(p, re.IGNORECASE) for p in HAIKU_PATTERNS]

# Cota dura: arriba de este largo asumimos pregunta elaborada → Sonnet.
HAIKU_MAX_LEN = 60


def decide_model(user_message: str, *, force: str | None = None) -> str:
    """Decide entre 'haiku' y 'sonnet' para el turno actual.

    Default: sonnet. Haiku SOLO si:
    - Saludo / smalltalk evidente, O
    - Mensaje corto (< HAIKU_MAX_LEN chars) que matchea un patrón de lookup puro.

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

    if len(text) > HAIKU_MAX_LEN:
        return "sonnet"

    for regex in _HAIKU_RE:
        if regex.search(text):
            return "haiku"

    return "sonnet"
